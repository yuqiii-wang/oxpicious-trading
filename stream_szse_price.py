"""stream_szse_price.py — Stream SZSE equity prices and build 5-minute OHLCV bars.

Targets SZSE-listed stocks that appear in ETF holdings (any weight),
retrieved from stats.stock_identity joined with stats.sec_composition.

Architecture:
  * Round-based streaming (no rigid biz-hour gating). The target list is split
    into N groups placed on a shared asyncio.Queue.
  * FOUR parallel async worker procs pull from the queue concurrently:
      proc A (akshare)     — AkShare ``ak.stock_zh_a_minute`` (Sina 1-min).
      proc C (em_push2his) — East Money push2his trends2 (akshare's
                             ``stock_zh_a_hist_min_em`` endpoint, ndays=5).
      proc D (em_push2)    — East Money push2 trends2 (akshare's
                             ``stock_zh_a_hist_pre_min_em`` endpoint, ndays=1,
                             iscr=1; a DIFFERENT host from C).
      proc B (szse)        — SZSE ``/api/market/ssjjhq/getTimeData``.
    A, C, D all pull from the primary queue; B drains the fallback queue first
    then the primary. Whoever finishes a group first takes the next group
    (dynamic dispatch). Each worker applies its own anti-bot cooldown between
    fetches; the four hit different hosts (Sina, push2his.eastmoney.com,
    push2.eastmoney.com, szse.cn) so they don't contend on rate limits.
    Crucially only proc A ever touches V8, so the old
    partition_address_space.cc(243) race cannot happen.
  * Note on C/D: East Money's hosts require TLS renegotiation that Python's
    stdlib ssl rejects (RemoteDisconnected). akshare's own functions call
    these endpoints with a plain requests.get and fail; C/D therefore use
    curl_cffi (libcurl-backed, impersonate='chrome') which handles the
    renegotiation reliably. The 09:30 open-snapshot point and any pre-market
    points (D returns 09:15+) are dropped so 5-min windowing matches source A.
  * Error handoff: if proc A/C/D fails on a stock (4xx / timeout) it returns
    the not-yet-finished remainder of that group to a fallback queue, which
    proc B resumes with the SZSE source. A hard exception in any worker
    requeues the unfinished group for B to resume.
  * After each round the loop checks which stocks' latest in-day bar time has
    NOT yet reached the close (15:00). Those not-yet-finished stocks are
    collected and the loop goes around again until every stock has reached
    close.
  * When all stocks of the current biz day are finished the loop waits for the
    next trading day and re-anchors. If a new trading day arrives mid-rounding,
    the loop anchors to the new day (state reset).
  * No-advance backoff: if a round completes with no stock advancing (pre-open
    or lunch break), the loop backs off instead of hammering the API.
  * Primary sources (parallel):
      A: AkShare ``ak.stock_zh_a_minute`` (Sina 1-minute bars).
      C: East Money push2his trends2 — same endpoint as akshare's
         ``stock_zh_a_hist_min_em(period='1')`` (ndays=5, iscr=0).
      D: East Money push2 trends2 — same endpoint as akshare's
         ``stock_zh_a_hist_pre_min_em`` (ndays=1, iscr=1, iscca=0).
  * Fallback source: SZSE ``/api/market/ssjjhq/getTimeData`` — the minute API
    discovered from network requests on the SZSE trend page
    (https://www.szse.cn/market/trend/index.html?code=000672).
  * Each fetch runs in a worker thread with a hard FETCH_TIMEOUT_SEC (120s)
    timeout — a hung fetch that downloads nothing for 2 min is abandoned and
    logged, never stalling the stream.
  * 1-minute samples are aggregated into 5-minute OHLCV bars:
      open       = first minute's close (last price) in the window
      high       = max of all minute closes
      low        = min of all minute closes
      close      = last minute's close
      volume     = sum of per-minute volumes across the window
      change     = close - open
      change_pct = (close - open) / open * 100
  * Bars are archived to CSV (temps/szse_intraday/) and upserted into
    stats.stock_intraday_5min (FK parent stats.stock_identity). The table is
    expected to pre-exist (created by database/sql/06_stock_baseline.sql).

Anti-bot: reuses safe_get(), build_headers_with_referer(), random_sleep,
HostStatusTracker and build_default_session() from _download_commons.py.
Cooldown: random_sleep(DEFAULT_SLEEP_SEC) per worker between fetches
(jittered [10,30]s); NO_ADVANCE_BACKOFF_SEC (60s) when no stock advanced.

Termination: Ctrl-C cancels both async workers (state.stop + task.cancel) and
exits cleanly after reaping them; the finally block closes the DB and session.

Requires tables from database/sql/06_stock_baseline.sql (stock_identity +
stock_intraday_5min) and database/sql/03_sec_composition.sql (sec_composition).

Usage:
  python stream_szse_price.py                  # stream (rounds until all reach close)
  python stream_szse_price.py --once           # one round then exit
  python stream_szse_price.py --groups 5       # 5 groups (sequential, dev)
  python stream_szse_price.py --interval 1     # 1s cooldown between fetches (dev)
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import locale as _locale
import sys
import time as _time
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

from _download_commons import (
    DEFAULT_SLEEP_SEC,
    HostStatusTracker,
    add_exchange_suffix,
    build_default_session,
    build_headers_with_referer,
    is_trading_day,
    random_sleep,
    resolve_out_dir,
    safe_get,
    setup_logger,
)
from _db_commons import (
    bulk_upsert,
    check_stock_intraday_exists,
    get_db_connection,
)
from _study_and_select_stocks import (
    TARGET_LOOKBACK_DAYS,
    load_target_stocks,
)

# ---------------------------------------------------------------------------
# stdout encoding (Windows)
# ---------------------------------------------------------------------------
try:
    _locale.setlocale(_locale.LC_ALL, "")
except Exception:
    pass
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


logger = setup_logger("stream_szse")

# Module-load timestamp — used by main() to log total time from import to
# stream() entry, so we can see whether top-level imports (pandas via
# _download_commons, requests, etc.) are the slow part.
_MODULE_LOAD_T0 = _time.time()
logger.info("[startup] module loaded; top-level imports done @ %.2fs.",
            _time.time() - _MODULE_LOAD_T0)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
# A stock is "finished" for its biz day once its latest 1-minute bar reaches
# the market close (15:00). Rounds keep re-fetching unfinished stocks until
# they all reach CLOSE_TIME, then the loop waits for the next trading day.
CLOSE_TIME = time(15, 0)

# When a round completes but no stock advanced (e.g. pre-open or lunch break),
# back off this long before the next round instead of hammering the API.
NO_ADVANCE_BACKOFF_SEC = 60.0

# Number of groups the target stock list is split into. Groups are processed
# SEQUENTIALLY (one after another, single thread) — not concurrently.
DEFAULT_GROUPS = 10

# Emit a progress line every N stocks processed.
PROGRESS_EVERY = 25

# Per-fetch hard timeout (seconds). If a single AkShare / SZSE fetch takes
# longer than this we abandon it (daemon thread winds down in the background)
# and log a timeout — so a hung fetch never stalls the whole stream.
FETCH_TIMEOUT_SEC = 120.0

# SZSE trend-page minute API (the JSON backing the "分时" chart on
# https://www.szse.cn/market/trend/index.html?code=<code>).
SZSE_TIMEDATA_URL = "https://www.szse.cn/api/market/ssjjhq/getTimeData"
SZSE_REFERER = "https://www.szse.cn/market/trend/index.html"

# Local CSV archive: one file per cycle, written under temps/szse_intraday/.
CSV_COLUMNS = [
    "update_time", "date", "code", "name", "time",
    "open", "high", "low", "close", "volume", "change", "change_pct",
]

# A normalized 1-minute sample from either source.
#   dt     : datetime of the bar (e.g. 2025-07-23 09:31:00)
#   price  : last price for that minute
#   volume : per-minute volume (shares)
MinuteSample = Tuple[datetime, float, float]


# ---------------------------------------------------------------------------
# Biz-day helpers
# ---------------------------------------------------------------------------
# The loop no longer gates on trading sessions; instead it runs rounds and
# anchors itself to a trading day. ``is_trading_day`` still tells us which
# calendar days are valid biz days.

def _next_trading_day_after(d) -> "date":
    """First trading day strictly after ``d``."""
    nd = d + timedelta(days=1)
    while not is_trading_day(nd):
        nd += timedelta(days=1)
    return nd


def wait_for_next_trading_day(current_biz_day, chunk_sec: float = 5.0) -> "date":
    """Sleep (in chunks, cancellation-responsive) until a new trading day.

    Returns the new trading day's date. Used after all stocks of the current
    biz day have reached CLOSE_TIME, before anchoring to the next biz day.
    """
    nxt = _next_trading_day_after(current_biz_day)
    while datetime.now().date() < nxt:
        _time.sleep(chunk_sec)
    return nxt


def sleep_chunks(sec: float, chunk_sec: float = 5.0) -> None:
    """Sleep for ``sec`` in chunks so Ctrl-C stays responsive (sync)."""
    end = _time.time() + max(0.0, sec)
    while _time.time() < end:
        _time.sleep(min(chunk_sec, max(0.0, end - _time.time())))


# ---------------------------------------------------------------------------
# Target stock list: see _study_and_select_stocks.load_target_stocks
# (SZSE stocks currently held by ETFs — imported above).
# ---------------------------------------------------------------------------


def split_groups(stocks: List[Tuple[str, str]], n: int) -> List[List[Tuple[str, str]]]:
    """Split stocks into ``n`` near-equal groups (round-robin for balance).

    Kept for compatibility / callers, though the main loop no longer runs
    groups concurrently — it streams stocks one at a time on a single thread.
    """
    groups: List[List[Tuple[str, str]]] = [[] for _ in range(n)]
    for i, item in enumerate(stocks):
        groups[i % n].append(item)
    return [g for g in groups if g]


# ---------------------------------------------------------------------------
# Primary source: AkShare ak.stock_zh_a_minute (Sina 1-minute bars)
# ---------------------------------------------------------------------------
_akshare = None


def _get_akshare():
    """Lazy-import akshare so the module loads even if akshare is absent."""
    global _akshare
    if _akshare is None:
        try:
            import akshare as _ak
        except ImportError as e:
            raise ImportError(
                "akshare is required for stream_szse_price.py primary source. "
                "Install with: pip install akshare"
            ) from e
        _akshare = _ak
    return _akshare


async def _fetch_async(source: str, bare_code: str, session, host_tracker):
    """Fetch one stock's minute samples via the given source.

    Runs the blocking fetch in a worker thread (so the event loop stays free
    for the other async worker) with a hard FETCH_TIMEOUT_SEC timeout. Returns
    the samples list, or None on failure / timeout (the "no data downloaded
    for 2 min → timeout" notice fires here).
    """
    if source == "akshare":
        coro = asyncio.to_thread(fetch_akshare_minute, bare_code)
    elif source == "em_push2his":
        coro = asyncio.to_thread(fetch_em_push2his_minute, bare_code)
    elif source == "em_push2":
        coro = asyncio.to_thread(fetch_em_push2_minute, bare_code)
    else:
        coro = asyncio.to_thread(fetch_szse_minute, session, bare_code, host_tracker)
    try:
        return await asyncio.wait_for(coro, timeout=FETCH_TIMEOUT_SEC)
    except asyncio.TimeoutError:
        logger.warning("[timeout] %s %s exceeded %.0fs — no data downloaded; skipping.",
                       source, bare_code, FETCH_TIMEOUT_SEC)
        return None
    except Exception as e:  # noqa: BLE001
        logger.warning("[%s %s] raised: %s", source, bare_code, e)
        return None


def fetch_akshare_minute(bare_code: str) -> Optional[List[MinuteSample]]:
    """Fetch today's 1-minute bars for one SZSE stock via AkShare.

    Returns a list of (datetime, close, volume) samples, or None when the
    request fails / is blocked (treated as a 4xx trigger for the fallback).
    """
    ak = _get_akshare()
    symbol = f"sz{bare_code}"
    try:
        df = ak.stock_zh_a_minute(symbol=symbol, period="1", adjust="")
    except Exception as e:
        logger.warning("[akshare %s] call failed: %s", symbol, e)
        return None
    if df is None or len(df) == 0:
        return None

    # Columns: day, open, high, low, close, volume
    samples: List[MinuteSample] = []
    for _, row in df.iterrows():
        day_val = row.get("day")
        close = row.get("close")
        vol = row.get("volume")
        if day_val is None or close is None:
            continue
        try:
            dt = datetime.strptime(str(day_val), "%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            try:
                dt = datetime.strptime(str(day_val), "%Y-%m-%d %H:%M")
            except (ValueError, TypeError):
                continue
        try:
            price = float(close)
        except (ValueError, TypeError):
            continue
        try:
            volume = float(vol) if vol is not None else 0.0
        except (ValueError, TypeError):
            volume = 0.0
        samples.append((dt, price, volume))
    return samples or None


# ---------------------------------------------------------------------------
# Fallback source: SZSE /api/market/ssjjhq/getTimeData
# ---------------------------------------------------------------------------
def fetch_szse_minute(
    session: requests.Session,
    bare_code: str,
    host_tracker: HostStatusTracker,
) -> Optional[List[MinuteSample]]:
    """Fetch intraday minute samples for one SZSE stock from the SZSE trend API.

    Returns a list of (datetime, price, volume), or None on failure / block.

    The SZSE ssjjhq endpoint returns JSON describing the "分时" chart for one
    code. Response shape is handled defensively: a ``data`` list (or numeric-
    keyed dict) of points, each exposing a time/price/volume field under one
    of several common key names.
    """
    headers = build_headers_with_referer(SZSE_REFERER)
    params = {"marketId": "1", "code": bare_code}
    resp = safe_get(
        session,
        SZSE_TIMEDATA_URL,
        params=params,
        headers=headers,
        host_tracker=host_tracker,
        logger=logger,
        log_tag=f"[szse {bare_code}] ",
    )
    if resp is None:
        return None
    try:
        payload = resp.json()
    except ValueError:
        logger.warning("[szse %s] non-JSON response", bare_code)
        return None

    # SZSE API uses code="0" for success, "-1" for error.
    if isinstance(payload, dict):
        api_code = payload.get("code")
        if str(api_code) != "0":
            logger.warning("[szse %s] API code=%s msg=%s",
                           bare_code, api_code, payload.get("message"))
            return None
    return _parse_szse_picupdata(payload, bare_code)


def _parse_szse_picupdata(payload, bare_code: str) -> Optional[List[MinuteSample]]:
    """Parse the SZSE getTimeData ``data.picupdata`` array into minute samples.

    Each entry: ["09:30", "10.92", "10.92", "-0.06", "-0.55", 4045, 4417140.0]
    Fields: time, open, close(now), delta, deltaPct, volume, amount
    """
    data_obj = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data_obj, dict):
        logger.warning("[szse %s] missing 'data' object in response", bare_code)
        return None

    picupdata = data_obj.get("picupdata")
    if not isinstance(picupdata, list) or not picupdata:
        logger.warning("[szse %s] missing or empty 'picupdata'", bare_code)
        return None

    # Use marketTime if available for the date, else today.
    market_time = data_obj.get("marketTime", "")
    try:
        trade_date = datetime.strptime(market_time[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        trade_date = datetime.now().date()

    samples: List[MinuteSample] = []
    for pt in picupdata:
        if not isinstance(pt, (list, tuple)) or len(pt) < 6:
            continue
        try:
            time_str = str(pt[0]).strip()       # "09:30"
            price = float(pt[2])                 # close/now price
            volume = float(pt[5])                # per-minute volume
        except (ValueError, TypeError, IndexError):
            continue
        try:
            t = datetime.strptime(time_str, "%H:%M").time()
        except ValueError:
            continue
        dt = datetime.combine(trade_date, t)
        samples.append((dt, price, volume))

    if not samples:
        logger.warning("[szse %s] no samples parsed from %d picupdata entries",
                       bare_code, len(picupdata))
    return samples or None


# ---------------------------------------------------------------------------
# Parallel akshare sources C & D: East Money trends2 (via curl_cffi)
# ---------------------------------------------------------------------------
# East Money's push2his/push2 hosts require TLS renegotiation that Python's
# stdlib ssl rejects (RemoteDisconnected). curl_cffi (libcurl-backed,
# impersonate='chrome') handles the renegotiation reliably, so both EM
# sources go through curl_cffi instead of the shared requests session.
# These mirror the endpoints akshare's ``stock_zh_a_hist_min_em`` (push2his,
# ndays=5) and ``stock_zh_a_hist_pre_min_em`` (push2, ndays=1, iscr=1) use,
# but akshare's own functions call them with a plain requests.get that fails
# the renegotiation — hence the direct curl_cffi implementation here.
EM_PUSH2HIS_URL = "https://push2his.eastmoney.com/api/qt/stock/trends2/get"
EM_PUSH2_URL = "https://push2.eastmoney.com/api/qt/stock/trends2/get"
EM_REFERER = "https://quote.eastmoney.com/"
EM_UT = "7eea3edcaed734bea9cbfc24409ed989"

# Regular-session minute bounds, matching the Sina (source A) convention:
# first morning bar 09:31, last 11:30; first afternoon 13:01, last 15:00.
# The 09:30 East Money open-snapshot point and any pre-market points (D
# returns 09:15+) are dropped so 5-min windowing lines up with source A
# (no spurious 09:30 bar).
_EM_MORNING_START = time(9, 31)
_EM_MORNING_END = time(11, 30)
_EM_AFTERNOON_START = time(13, 1)
_EM_AFTERNOON_END = time(15, 0)

_em_session = None


def _get_em_session():
    """Lazy-create a persistent curl_cffi.requests.Session for East Money.

    A Session reuses TLS connections (TLS session resumption) across requests,
    which reduces the chance of East Money closing the connection (curl error
    56). The ``impersonate='chrome'`` fingerprint is set once on the Session.
    """
    global _em_session
    if _em_session is None:
        try:
            from curl_cffi import requests as _cr
        except ImportError as e:
            raise ImportError(
                "curl_cffi is required for the East Money parallel sources "
                "(C/D) in stream_szse_price.py. Install with: pip install curl_cffi"
            ) from e
        _em_session = _cr.Session(impersonate="chrome")
    return _em_session


def _em_secid(bare_code: str) -> str:
    """East Money secid: market 0 for SZ/BJ, 1 for SH. All targets are SZSE."""
    market_code = 1 if bare_code.startswith("6") else 0
    return f"{market_code}.{bare_code}"


def _is_em_regular_session(t: time) -> bool:
    """True for minutes in the regular trading session (Sina convention)."""
    return (_EM_MORNING_START <= t <= _EM_MORNING_END) or \
           (_EM_AFTERNOON_START <= t <= _EM_AFTERNOON_END)


def _parse_em_trends(trends, bare_code: str, source_tag: str) -> Optional[List[MinuteSample]]:
    """Parse East Money trends2 ``trends`` CSV list into MinuteSamples.

    Each entry: "YYYY-MM-DD HH:MM,open,close,high,low,volume,amount,avg".
    Uses [0]=datetime, [2]=close (last price), [5]=volume. Drops the 09:30
    open-snapshot and pre-market points so 5-min windowing matches source A.
    """
    samples: List[MinuteSample] = []
    for item in trends:
        parts = item.split(",")
        if len(parts) < 6:
            continue
        time_str = parts[0]
        try:
            dt = datetime.strptime(time_str, "%Y-%m-%d %H:%M")
        except ValueError:
            try:
                dt = datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
        if not _is_em_regular_session(dt.time()):
            continue
        try:
            price = float(parts[2])
        except (ValueError, TypeError):
            continue
        try:
            volume = float(parts[5]) if parts[5] != "" else 0.0
        except (ValueError, TypeError):
            volume = 0.0
        samples.append((dt, price, volume))
    if not samples:
        logger.warning("[%s %s] no regular-session samples parsed from %d trends",
                       source_tag, bare_code, len(trends))
    return samples or None


def _em_get_trends(url: str, params: dict, bare_code: str, source_tag: str,
                   retries: int = 2) -> Optional[list]:
    """GET an East Money trends2 endpoint via a persistent curl_cffi Session.

    Returns the parsed ``data.trends`` list, or None on failure. Uses a
    Session (TLS connection reuse) to reduce curl error 56 (connection closed
    abruptly). If the Session raises a connection error, it is recreated once
    in case the pooled connection went stale.
    """
    sess = _get_em_session()
    headers = {"Referer": EM_REFERER}
    data = None
    err = "no attempts"
    for attempt in range(retries + 1):
        try:
            r = sess.get(url, params=params, headers=headers, timeout=15)
        except Exception as e:  # noqa: BLE001
            err = f"{type(e).__name__}: {e}"
            # Recreate the Session once on connection errors (stale pool).
            global _em_session
            _em_session = None
            sess = _get_em_session()
            if attempt < retries:
                _time.sleep(1.5)
            continue
        if r.status_code != 200:
            err = f"HTTP {r.status_code}"
            if attempt < retries:
                _time.sleep(1.5)
            continue
        try:
            data = r.json()
        except ValueError as e:
            err = f"non-JSON: {e}"
            if attempt < retries:
                _time.sleep(1.5)
            continue
        break
    if data is None:
        logger.warning("[%s %s] trends2 fetch failed after retries: %s",
                       source_tag, bare_code, err)
        return None
    data_obj = data.get("data") if isinstance(data, dict) else None
    if not isinstance(data_obj, dict):
        logger.warning("[%s %s] missing 'data' object", source_tag, bare_code)
        return None
    trends = data_obj.get("trends")
    if not isinstance(trends, list) or not trends:
        logger.warning("[%s %s] missing or empty 'trends'", source_tag, bare_code)
        return None
    return trends


def fetch_em_push2his_minute(bare_code: str) -> Optional[List[MinuteSample]]:
    """Source C: East Money push2his trends2 (5-day 1-min bars, iscr=0).

    Mirrors akshare ``stock_zh_a_hist_min_em(period='1')`` — hits
    push2his.eastmoney.com with ndays=5. aggregate_5min keeps only the
    current trade_date's samples, so the 5-day span is fine.
    """
    params = {
        "fields1": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
        "ut": EM_UT,
        "ndays": "5",
        "iscr": "0",
        "secid": _em_secid(bare_code),
    }
    trends = _em_get_trends(EM_PUSH2HIS_URL, params, bare_code, "C")
    if trends is None:
        return None
    return _parse_em_trends(trends, bare_code, "C")


def fetch_em_push2_minute(bare_code: str) -> Optional[List[MinuteSample]]:
    """Source D: East Money push2 trends2 (1-day, iscr=1, with pre-market).

    Mirrors akshare ``stock_zh_a_hist_pre_min_em`` — hits push2.eastmoney.com
    (a DIFFERENT host from C) with ndays=1, iscr=1. Pre-market points
    (09:15-09:30) are dropped by _parse_em_trends.
    """
    params = {
        "fields1": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
        "ndays": "1",
        "iscr": "1",
        "iscca": "0",
        "secid": _em_secid(bare_code),
    }
    trends = _em_get_trends(EM_PUSH2_URL, params, bare_code, "D")
    if trends is None:
        return None
    return _parse_em_trends(trends, bare_code, "D")


# ---------------------------------------------------------------------------
# 5-minute OHLCV aggregation
# ---------------------------------------------------------------------------
def _window_end_minute(bar_dt: datetime) -> time:
    """Return the end time of the 5-minute window a 1-minute bar falls into.

    Bars 09:31-09:35 -> 09:35; 09:36-09:40 -> 09:40; 13:01-13:05 -> 13:05.
    Uses the convention window = (end-5, end]; the lunch break leaves no
    stray windows because no bars exist between 11:30 and 13:01.
    """
    m = bar_dt.hour * 60 + bar_dt.minute
    end = ((m - 1) // 5 + 1) * 5
    return time(end // 60, end % 60)


def aggregate_5min(
    bare_code: str,
    name: str,
    samples: List[MinuteSample],
    emitted: set,
    trade_date,
) -> Tuple[List[dict], List[dict], Optional[time]]:
    """Aggregate 1-minute samples into 5-minute OHLCV bars for ``trade_date``.

    Samples whose date does not match ``trade_date`` are dropped — this guards
    against AkShare handing back the previous day's bars before today's session
    opens (which would otherwise make a stock look "finished" at 15:00 of the
    wrong day). Emits a window when its end time is at or before the latest
    available bar (so the window is fully populated) and it has not been
    emitted before.

    Args:
        emitted: mutable set of already-emitted window-end ``time`` objects
            for this stock (cleared when the loop anchors to a new biz day).
        trade_date: the biz day we are currently collecting.

    Returns (identity_rows, bar_rows, latest_time). ``latest_time`` is the
    latest in-day bar time seen (or None if no in-day samples), used to tell
    whether the stock has reached CLOSE_TIME.
    """
    if not samples:
        return [], [], None

    full_code = add_exchange_suffix(bare_code, "深圳")
    # Exchange suffix: only SZ, SS, or BJ are valid (Beijing Stock Exchange).
    # Other codes like SH/HK are not used for stocks in this project.
    parts = full_code.rsplit(".", 1)
    code_suffix = parts[-1] if len(parts) == 2 and parts[-1] in ("SZ", "SS", "BJ") else None

    # Keep only samples that belong to the current biz day.
    in_day: List[MinuteSample] = [s for s in samples if s[0].date() == trade_date]
    if not in_day:
        return [], [], None

    # Group samples by window-end time, preserving order within a window.
    windows: Dict[time, List[MinuteSample]] = {}
    for s in in_day:
        wend = _window_end_minute(s[0])
        windows.setdefault(wend, []).append(s)

    last_bar_minute = max(s[0].hour * 60 + s[0].minute for s in in_day)
    latest_time = time(last_bar_minute // 60, last_bar_minute % 60)

    identity_rows: List[dict] = []
    bar_rows: List[dict] = []
    for wend, pts in sorted(windows.items(), key=lambda kv: kv[0]):
        wend_minute = wend.hour * 60 + wend.minute
        # Skip the window that is still in progress (its end time is after
        # the latest bar we received -> more bars may still arrive).
        if wend_minute > last_bar_minute:
            continue
        if wend in emitted:
            continue
        # Order points by their own timestamp within the window.
        pts.sort(key=lambda x: x[0])
        prices = [p for _, p, _ in pts]
        vols = [v for _, _, v in pts]
        o = prices[0]
        h = max(prices)
        low = min(prices)
        c = prices[-1]
        vol = sum(vols)
        change = round(c - o, 4)
        change_pct = round((c - o) / o * 100, 4) if o else None

        # is_in_etf=True by construction: load_target_stocks() pre-filters the
        # streaming target list to stocks whose latest stock_identity row (last
        # 30 days) has is_in_etf=TRUE, so every identity row emitted here is
        # for an ETF-held stock. Setting it explicitly keeps new (date, code)
        # rows from defaulting to FALSE before the next backfill runs.
        identity_rows.append({
            "date": trade_date,
            "code": full_code,
            "code_suffix": code_suffix,
            "name": name,
            "is_in_etf": True,
        })
        bar_rows.append({
            "date": trade_date,
            "code": full_code,
            "code_suffix": code_suffix,
            "time": wend,
            "open": o,
            "high": h,
            "low": low,
            "close": c,
            "volume": vol,
            "change": change,
            "change_pct": change_pct,
        })
        emitted.add(wend)
    return identity_rows, bar_rows, latest_time


# ---------------------------------------------------------------------------
# Group processing (synchronous, one stock at a time): AkShare primary,
# SZSE fallback on 4xx / failure
# ---------------------------------------------------------------------------
async def _process_stocks(
    source: str,
    stocks: List[Tuple[str, str]],
    session: requests.Session,
    host_tracker: HostStatusTracker,
    emitted_map: Dict[str, set],
    trade_date,
    cooldown_sec: float,
    tag: str = "",
    conn=None,
) -> Tuple[List[dict], List[dict], Dict[str, Optional[time]], List[Tuple[str, str]]]:
    """Process a list of stocks with ONE source, async.

    One request per stock, with the anti-bot cooldown (random_sleep) between
    fetches (run in a thread so the event loop stays free for the sibling
    worker). Per-stock INFO logging shows data flowing from each source.

    Circuit breaker: each source has a ``max_consecutive_failures`` threshold.
    When that many consecutive failures occur, the worker returns the remainder
    (from the current failed stock onward) so the szse worker can resume it.
      * akshare (A): 1  — stop immediately on first failure (V8 crash risk).
      * em_push2his/push2 (C/D): 3  — ride through 1-2 transient failures
        before giving up (EM endpoints can be flaky but not uniformly dead).
      * szse (B): 0  — never stops (last resort, always continues).
    Returns (identity_rows, bar_rows, {code: latest_time}, remainder).
    """
    if source == "akshare":
        max_consecutive_failures = 1
    elif source in ("em_push2his", "em_push2"):
        max_consecutive_failures = 3
    else:  # szse — last resort, never give up
        max_consecutive_failures = 0
    consecutive_failures = 0

    identity_rows: List[dict] = []
    bar_rows: List[dict] = []
    latest_times: Dict[str, Optional[time]] = {}
    n_stocks = len(stocks)
    t_start = _time.time()

    for i, (bare_code, name) in enumerate(stocks):
        ft0 = _time.time()
        samples = await _fetch_async(source, bare_code, session, host_tracker)
        fe = _time.time() - ft0
        if samples is not None:
            consecutive_failures = 0  # reset on success
            emitted = emitted_map.setdefault(bare_code, set())
            ident, bars, lt = aggregate_5min(bare_code, name, samples, emitted, trade_date)
            latest_times[bare_code] = lt
            identity_rows.extend(ident)
            bar_rows.extend(bars)
            
            # Check if we got samples but 0 bars (likely date mismatch)
            if len(bars) == 0 and len(samples) > 0:
                # Check if data already exists for this stock on trade_date
                data_existed = False
                if conn is not None:
                    full_code = add_exchange_suffix(bare_code, "深圳")
                    data_existed = await asyncio.to_thread(
                        check_stock_intraday_exists, conn, full_code, trade_date
                    )
                
                if data_existed:
                    logger.info(
                        "%s[%s] %s: %d samples -> 0 bars, data existed for %s, skipped",
                        tag, source, bare_code, len(samples), trade_date,
                    )
                else:
                    logger.info(
                        "%s[%s] %s: %d samples -> 0 bars (latest=%s) in %.1fs",
                        tag, source, bare_code, len(samples), lt, fe,
                    )
            else:
                logger.info(
                    "%s[%s] %s: %d samples -> %d bars (latest=%s) in %.1fs",
                    tag, source, bare_code, len(samples), len(bars), lt, fe,
                )
            # Incremental DB upsert so each stock's bars appear in
            # stats.stock_intraday_5min immediately (not only at round-end).
            if conn is not None and (ident or bars):
                up_t0 = _time.time()
                try:
                    await asyncio.to_thread(load_bars_sync, conn, ident, bars)
                    logger.info(
                        "%s[%s] %s: upserted %d identity + %d bars in %.2fs",
                        tag, source, bare_code, len(ident), len(bars),
                        _time.time() - up_t0,
                    )
                except Exception as e:  # noqa: BLE001
                    logger.error(
                        "%s[%s] %s: DB upsert failed: %s", tag, source, bare_code, e,
                    )
        else:
            latest_times[bare_code] = None
            consecutive_failures += 1
            logger.info("%s[%s] %s: NO DATA in %.1fs (consecutive_failures=%d/%d)",
                        tag, source, bare_code, fe, consecutive_failures,
                        max_consecutive_failures if max_consecutive_failures > 0 else -1)
            if max_consecutive_failures > 0 and consecutive_failures >= max_consecutive_failures:
                # Circuit breaker tripped: hand the rest of this group back so
                # the szse worker can resume it.
                remainder = stocks[i:]
                logger.info(
                    "%s[%s] circuit breaker tripped after %d consecutive failures on %s (%d/%d); "
                    "returning %d remaining stocks for szse resume",
                    tag, source, consecutive_failures, bare_code, i + 1, n_stocks, len(remainder),
                )
                return identity_rows, bar_rows, latest_times, remainder

        if (i + 1) % PROGRESS_EVERY == 0 or (i + 1) == n_stocks:
            logger.info(
                "%s[%s] progress: %d/%d stocks in %.1fs, bars so far=%d",
                tag, source, i + 1, n_stocks, _time.time() - t_start, len(bar_rows),
            )

        # Anti-bot cooldown between fetches (skip after the last stock).
        if i < n_stocks - 1:
            await asyncio.to_thread(random_sleep, cooldown_sec)

    return identity_rows, bar_rows, latest_times, []


# ---------------------------------------------------------------------------
# CSV archive
# ---------------------------------------------------------------------------
def write_cycle_csv(cycle_dt: datetime, bar_rows: List[dict]) -> Optional[Path]:
    """Archive one cycle's emitted bars to a CSV under temps/szse_intraday/."""
    if not bar_rows:
        return None
    out_dir = resolve_out_dir(__file__, "szse_intraday", None)
    ts = cycle_dt.strftime("%Y%m%d_%H%M%S")
    out_file = out_dir / f"szse_intraday_{ts}.csv"
    iso = cycle_dt.strftime("%Y-%m-%d %H:%M:%S")

    with open(out_file, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in bar_rows:
            out = {"update_time": iso}
            out.update(row)
            writer.writerow(out)
    return out_file


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------
def load_bars_sync(conn, identity_rows: List[dict], bar_rows: List[dict]) -> None:
    """Upsert identity rows (FK parent) then intraday bars (sync).

    ``aggregate_5min`` emits one identity row per 5-min bar, so a single stock
    with N bars yields N identical ``{date, code, name}`` rows. A multi-row
    INSERT ... ON CONFLICT (date, code) with duplicate keys raises
    "cannot affect row a second time", so collapse identity rows to one per
    (date, code) before upserting.
    """
    if identity_rows:
        seen = set()
        uniq: List[dict] = []
        for r in identity_rows:
            k = (r["date"], r["code"])
            if k in seen:
                continue
            seen.add(k)
            uniq.append(r)
        bulk_upsert(conn, "stats.stock_identity", uniq, ["date", "code"])
    if bar_rows:
        bulk_upsert(conn, "stats.stock_intraday_5min", bar_rows, ["date", "code", "time"])


# ---------------------------------------------------------------------------
# Main streaming loop — rounds, anchoring, clean Ctrl-C
# ---------------------------------------------------------------------------
def _not_finished(lt: Optional[time]) -> bool:
    """A stock is unfinished until its latest in-day bar reaches CLOSE_TIME."""
    return lt is None or lt < CLOSE_TIME


# ---------------------------------------------------------------------------
# Async workers: three primary procs (A=akshare, C=em_push2his, D=em_push2)
# pull groups from a shared primary queue in parallel; one fallback proc
# (B=szse) drains the fallback queue first, then the primary. Whoever finishes
# first takes the next group. On an A/C/D failure the unfinished remainder is
# handed to the SZSE worker. V8 is only ever touched by the akshare worker, so
# the partition_address_space.cc race cannot happen.
# ---------------------------------------------------------------------------
async def _parallel_source_worker(source: str, tag: str, q_primary, q_fallback,
                                  state, session, host_tracker, emitted_map,
                                  trade_date, cooldown_sec, conn):
    """Generic primary-queue worker for sources A (akshare), C (em_push2his),
    D (em_push2). Pulls groups from q_primary; on failure hands the remainder
    to q_fallback for the szse worker to resume.
    """
    while True:
        if state["stop"]:
            return
        try:
            stocks = await asyncio.wait_for(q_primary.get(), timeout=0.5)
        except asyncio.TimeoutError:
            if state["n_outstanding"] <= 0 and q_fallback.empty():
                return
            continue
        if stocks is None:  # sentinel
            q_primary.task_done()
            return
        try:
            ident, bars, latests, remainder = await _process_stocks(
                source, stocks, session, host_tracker, emitted_map,
                trade_date, cooldown_sec, tag=tag, conn=conn,
            )
            state["identity"].extend(ident)
            state["bars"].extend(bars)
            _merge_latests(state["latest_bar_time"], latests)
            if remainder:
                # Hand the unfinished portion to the szse worker (fallback).
                await q_fallback.put(remainder)
                logger.info("%s: handed off %d stocks to szse worker.", tag, len(remainder))
            else:
                state["n_outstanding"] -= 1
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001  (work preservation)
            logger.exception("%s: error on a group (%s); requeuing for szse resume.", tag, e)
            await q_fallback.put(stocks)
        finally:
            try:
                q_primary.task_done()
            except ValueError:
                pass


async def _szse_worker(q_primary, q_fallback, state, session, host_tracker,
                       emitted_map, trade_date, cooldown_sec, conn):
    tag = "B"
    while True:
        if state["stop"]:
            return
        stocks = None
        from_fallback = True
        try:
            stocks = q_fallback.get_nowait()
        except asyncio.QueueEmpty:
            from_fallback = False
            try:
                stocks = await asyncio.wait_for(q_primary.get(), timeout=0.5)
            except asyncio.TimeoutError:
                if state["n_outstanding"] <= 0 and q_fallback.empty():
                    return
                continue
        if stocks is None:  # sentinel
            if not from_fallback:
                q_primary.task_done()
            return
        try:
            ident, bars, latests, _rem = await _process_stocks(
                "szse", stocks, session, host_tracker, emitted_map,
                trade_date, cooldown_sec, tag=tag, conn=conn,
            )
            state["identity"].extend(ident)
            state["bars"].extend(bars)
            _merge_latests(state["latest_bar_time"], latests)
            state["n_outstanding"] -= 1
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001  (work preservation)
            logger.exception("%s: error on a group (%s); giving up on it.", tag, e)
            state["n_outstanding"] -= 1
        finally:
            if from_fallback:
                try:
                    q_fallback.task_done()
                except ValueError:
                    pass
            else:
                try:
                    q_primary.task_done()
                except ValueError:
                    pass


def _merge_latests(shared: Dict[str, Optional[time]], new: Dict[str, Optional[time]]):
    """Forward-only merge of per-stock latest bar times."""
    for code, lt in new.items():
        if lt is None:
            if shared.get(code) is None:
                shared[code] = None
            continue
        prev = shared.get(code)
        if prev is None or lt > prev:
            shared[code] = lt


async def stream(
    poll_interval: float = DEFAULT_SLEEP_SEC,
    n_groups: int = DEFAULT_GROUPS,
    once: bool = False,
) -> None:
    # One connection per worker: psycopg connections are NOT safe for
    # concurrent use, so each async worker gets its own. Bars are upserted
    # per-stock (incrementally) so rows appear in stats.stock_intraday_5min as
    # soon as each stock is fetched. conn is for load_target_stocks only.
    t_stream = _time.time()
    logger.info("[startup] stream() entered; opening 5 DB connections...")

    t_step = _time.time()
    conn = get_db_connection()
    logger.info("[startup] DB conn (main) ready in %.2fs.", _time.time() - t_step)

    t_step = _time.time()
    conn_a = get_db_connection()
    logger.info("[startup] DB conn_a (akshare) ready in %.2fs.", _time.time() - t_step)

    t_step = _time.time()
    conn_b = get_db_connection()
    logger.info("[startup] DB conn_b (szse) ready in %.2fs.", _time.time() - t_step)

    t_step = _time.time()
    conn_c = get_db_connection()
    logger.info("[startup] DB conn_c (em_push2his) ready in %.2fs.", _time.time() - t_step)

    t_step = _time.time()
    conn_d = get_db_connection()
    logger.info("[startup] DB conn_d (em_push2) ready in %.2fs.", _time.time() - t_step)
    logger.info("[startup] all 5 DB connections ready in %.2fs total (stats.stock_intraday_5min expected to pre-exist).",
                _time.time() - t_stream)

    t0 = _time.time()
    logger.info("[startup] calling load_target_stocks(conn)...")
    stocks = load_target_stocks(conn)
    logger.info("[startup] Loaded %d target SZSE stocks (ETF weight >= 1%%) in %.2fs.",
                len(stocks), _time.time() - t0)
    if not stocks:
        logger.error("No target stocks found; ensure sec_composition is populated.")
        for c in (conn, conn_a, conn_b, conn_c, conn_d):
            c.close()
        return

    t0 = _time.time()
    groups = split_groups(stocks, n_groups)
    group_sizes = [len(g) for g in groups]
    logger.info(
        "[startup] stream_szse_price started: %d stocks -> %d groups (sizes: min=%d max=%d avg=%.1f); "
        "4 parallel procs (A=akshare, C=em_push2his, D=em_push2, B=szse); cooldown=%.0fs once=%s (split_groups in %.2fs)",
        len(stocks), len(groups), min(group_sizes), max(group_sizes),
        sum(group_sizes) / len(group_sizes) if group_sizes else 0,
        poll_interval, once, _time.time() - t0,
    )

    # Import AkShare up-front (heavy module: pandas/numpy/requests + V8).
    t0 = _time.time()
    logger.info("[startup] importing AkShare (heavy: pandas/numpy/requests + V8)...")
    _get_akshare()
    logger.info("[startup] AkShare imported (V8 ready) in %.2fs.", _time.time() - t0)
    # curl_cffi drives the East Money sources C/D (TLS renegotiation).
    t0 = _time.time()
    logger.info("[startup] creating curl_cffi Session (EM sources C/D)...")
    _get_em_session()
    logger.info("[startup] curl_cffi Session created (EM sources C/D ready) in %.2fs.", _time.time() - t0)

    t0 = _time.time()
    session = build_default_session()
    logger.info("[startup] build_default_session() ready in %.2fs.", _time.time() - t0)

    t0 = _time.time()
    host_tracker = HostStatusTracker()
    logger.info("[startup] HostStatusTracker() ready in %.2fs.", _time.time() - t0)
    logger.info("[startup] total startup time: %.2fs; entering main loop.", _time.time() - t_stream)

    # --- Per-biz-day state (reset whenever we anchor to a new biz day) ---
    current_biz_day = None
    latest_bar_time: Dict[str, Optional[time]] = {c: None for c, _ in stocks}
    emitted_map: Dict[str, set] = {}

    try:
        while True:
            today = datetime.now().date()
            trading_today = is_trading_day(today)

            # ---- Anchor: pick / refresh the biz day we are collecting ----
            if current_biz_day is None:
                if not trading_today:
                    logger.info("Today (%s) is not a trading day; waiting for next biz day.", today)
                    current_biz_day = await asyncio.to_thread(wait_for_next_trading_day, today)
                else:
                    current_biz_day = today
                latest_bar_time = {c: None for c, _ in stocks}
                emitted_map = {}
                logger.info("Anchored to biz day %s; %d stocks to stream.",
                            current_biz_day, len(stocks))
            elif today > current_biz_day and trading_today:
                logger.info("New biz day %s reached (was %s); re-anchoring.",
                            today, current_biz_day)
                current_biz_day = today
                latest_bar_time = {c: None for c, _ in stocks}
                emitted_map = {}

            # ---- Compute unfinished stocks (latest time < 15:00) ----
            unfinished = [(c, nm) for (c, nm) in stocks if _not_finished(latest_bar_time.get(c))]
            n_unfinished = len(unfinished)
            n_finished = len(stocks) - n_unfinished

            if n_unfinished == 0:
                logger.info("All %d stocks finished for biz day %s; waiting for next trading day.",
                            len(stocks), current_biz_day)
                if once:
                    break
                current_biz_day = await asyncio.to_thread(wait_for_next_trading_day, current_biz_day)
                latest_bar_time = {c: None for c, _ in stocks}
                emitted_map = {}
                logger.info("Anchored to biz day %s.", current_biz_day)
                continue

            # ---- Run ONE round via 4 parallel async workers ----
            round_groups = split_groups(unfinished, n_groups)
            round_start = _time.time()
            round_label = datetime.now().strftime("%H:%M:%S")
            logger.info(
                "=== round start @ %s biz=%s: %d unfinished / %d finished (groups=%d, 4 parallel procs: A/C/D primary + B fallback) ===",
                round_label, current_biz_day, n_unfinished, n_finished, len(round_groups),
            )

            prev_latest = dict(latest_bar_time)

            q_primary: asyncio.Queue = asyncio.Queue()
            q_fallback: asyncio.Queue = asyncio.Queue()
            for g in round_groups:
                q_primary.put_nowait(g)
            state = {
                "identity": [],
                "bars": [],
                "latest_bar_time": latest_bar_time,
                "n_outstanding": len(round_groups),
                "stop": False,
            }

            workers = [
                asyncio.create_task(_parallel_source_worker(
                    "akshare", "A", q_primary, q_fallback, state, session,
                    host_tracker, emitted_map, current_biz_day, poll_interval, conn_a)),
                asyncio.create_task(_parallel_source_worker(
                    "em_push2his", "C", q_primary, q_fallback, state, session,
                    host_tracker, emitted_map, current_biz_day, poll_interval, conn_c)),
                asyncio.create_task(_parallel_source_worker(
                    "em_push2", "D", q_primary, q_fallback, state, session,
                    host_tracker, emitted_map, current_biz_day, poll_interval, conn_d)),
                asyncio.create_task(_szse_worker(
                    q_primary, q_fallback, state, session, host_tracker,
                    emitted_map, current_biz_day, poll_interval, conn_b)),
            ]
            try:
                await asyncio.gather(*workers)
            except asyncio.CancelledError:
                state["stop"] = True
                for w in workers:
                    if not w.done():
                        w.cancel()
                await asyncio.gather(*workers, return_exceptions=True)
                raise

            round_elapsed = _time.time() - round_start
            all_identity = state["identity"]
            all_bars = state["bars"]

            advanced = any(latest_bar_time.get(c) != prev_latest.get(c) for c, _ in unfinished)

            # Bars were already upserted per-stock during the round; here we
            # only archive the full cycle to CSV and log the summary.
            if all_bars:
                csv_path = write_cycle_csv(datetime.now(), all_bars)
                codes_with_bars = len({r["code"] for r in all_bars})
                logger.info(
                    "=== round done @ %s: %d bars / %d stocks (identity=%d) "
                    "in %.1fs (%.0f bars/s); csv=%s (DB upserted per-stock) ===",
                    round_label, len(all_bars), codes_with_bars, len(all_identity),
                    round_elapsed,
                    len(all_bars) / round_elapsed if round_elapsed > 0 else 0.0,
                    csv_path.name if csv_path else "(none)",
                )
            else:
                logger.info(
                    "=== round done @ %s: no new bars in %.1fs ===",
                    round_label, round_elapsed,
                )

            n_finished_now = sum(1 for c, _ in stocks if not _not_finished(latest_bar_time.get(c)))
            logger.info(
                "progress: %d/%d stocks finished (%.1f%%), %d still < %s",
                n_finished_now, len(stocks), n_finished_now * 100.0 / len(stocks),
                len(stocks) - n_finished_now, CLOSE_TIME.strftime("%H:%M"),
            )

            if once:
                logger.info("--once set; exiting after one round.")
                break

            # ---- Pace the next round ----
            if not advanced:
                sleep_sec = NO_ADVANCE_BACKOFF_SEC
                logger.info("no stock advanced this round; backing off %.0fs.", sleep_sec)
            else:
                sleep_sec = poll_interval - round_elapsed
            if sleep_sec > 0:
                await asyncio.to_thread(sleep_chunks, sleep_sec)
    except (asyncio.CancelledError, KeyboardInterrupt):
        logger.info("Termination signal received; cancelling async workers and exiting.")
    finally:
        for c in (conn, conn_a, conn_b, conn_c, conn_d):
            try:
                c.close()
            except Exception:
                pass
        session.close()


def main() -> None:
    t_main = _time.time()
    logger.info("[startup] main() entered @ %.2fs after module load.",
                _time.time() - _MODULE_LOAD_T0)
    ap = argparse.ArgumentParser(
        description="Stream SZSE equity prices into 5-min OHLCV bars "
                    "(4 parallel procs: A=akshare, C=em_push2his, "
                    "D=em_push2, B=szse fallback)."
    )
    ap.add_argument("--interval", type=float, default=DEFAULT_SLEEP_SEC,
                    help=f"Cooldown between fetches per worker in seconds (default {DEFAULT_SLEEP_SEC}).")
    ap.add_argument("--groups", type=int, default=DEFAULT_GROUPS,
                    help=f"Number of groups to split stocks into (default {DEFAULT_GROUPS}).")
    ap.add_argument("--once", action="store_true",
                    help="Run one round then exit (dev/test).")
    args = ap.parse_args()
    logger.info("[startup] args parsed (interval=%.1f groups=%d once=%s) in %.2fs; calling asyncio.run(stream)...",
                args.interval, args.groups, args.once, _time.time() - t_main)
    try:
        asyncio.run(stream(
            poll_interval=args.interval,
            n_groups=max(1, args.groups),
            once=args.once,
        ))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
