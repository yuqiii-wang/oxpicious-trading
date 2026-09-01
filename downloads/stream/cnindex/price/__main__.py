"""Stream CNINDEX (国证指数) intraday data for CNINDEX-published indices.

Complements the SSE/SZSE/CSIndex streamers by fetching intraday 1-min bars
from cnindex.com.cn for indices like 国证2000 (399303), 国证1000 (399311)
and 国证A50 (399310) that are NOT covered by any other streamer.

API: https://hq.cnindex.com.cn/market/market/getIndexRealTimeData
  GET params: indexCode
  Response: data.data = array of 1-min bars [timestamp, current, high, open,
            low, close, chg, percent, amount, volume, avg, preClose].
            Null rows = non-trading minutes (lunch break, after close).

The 1-min bars are aggregated into 5-minute OHLC bars (ceiling convention,
same as SSE/SZSE/CSIndex streamers) and upserted into
``stats.index_intraday_5min`` (+ ``stats.index_identity`` as FK parent).

Scheduling (two time points per trading day):
  - Before 16:00: sleep and wait.
  - 16:00–16:30 (1st window): check DB for existing data; if none, fetch
    once per index. Then sleep until 16:30.
  - After 16:30 (2nd window): fetch once per index (final closing data).
    Then sleep until next day.

Usage:
  python -m downloads.stream.cnindex.price              # scheduled stream
  python -m downloads.stream.cnindex.price --once       # one immediate fetch then exit
  python -m downloads.stream.cnindex.price --code 399303 # fetch one code then exit
"""
from __future__ import annotations


import argparse
import logging
import sys
import time as _time
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Ensure project root is on sys.path when run via -m
_PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from downloads._common import (
    DEFAULT_SLEEP_SEC,
    AntiBotConfig,
    AntiBotProxy,
    HostStatusTracker,
    build_default_session,
    merge_browser_profile,
    setup_logger,
    random_sleep,
)
from downloads.stream.cnindex.price._cnindex_api import (
    CNINDEX_CODES,
    CNINDEX_HEADERS,
    CNINDEX_HQ_BASE,
    fetch_intraday_data,
    _ms_to_date,
    _to_float,
    COL_TIMESTAMP,
    COL_CURRENT,
    COL_HIGH,
    COL_OPEN,
    COL_LOW,
    COL_CLOSE,
    COL_PRECLOSE,
)
from _common.db_commons import bulk_upsert, get_db_connection
from _common._holidays_and_weekdays import is_trading_day

logger = setup_logger("cnindex_stream")

# Scheduling time points (two fetch windows per trading day, post-close).
FIRST_WINDOW_START = time(16, 0)    # 1st fetch: 16:00–16:30
SECOND_WINDOW_START = time(16, 30)  # 2nd fetch: after 16:30

# Non-trading-day sleep cadence (checked periodically).
NON_TRADING_SLEEP_SEC = 30 * 60  # 30 min

# Sleep chunk size for Ctrl-C responsiveness.
SLEEP_CHUNK_SEC = 5.0


# ---------------------------------------------------------------------------
# Aggregation: 1-min bars -> 5-min OHLC bars
# ---------------------------------------------------------------------------

def _ms_to_time(ms: Any) -> Optional[time]:
    """Convert epoch milliseconds to datetime.time."""
    if ms is None:
        return None
    try:
        return datetime.fromtimestamp(int(ms) / 1000).time()
    except (ValueError, TypeError, OSError):
        return None


def _window_end_5min(t: time) -> time:
    """Return the 5-minute window END time for a given tick time (ceiling).

    Uses the SAME ceiling convention as SSE/SZSE/CSIndex streamers:
    09:30 -> 09:30, 09:31-09:35 -> 09:35, 09:36-09:40 -> 09:40, ...
    """
    minute = t.hour * 60 + t.minute
    wend_minute = ((minute - 1) // 5 + 1) * 5
    h, m = divmod(wend_minute, 60)
    return time(h, m)


def aggregate_1min_to_5min(
    code: str,
    name: str,
    bars: List[List[Any]],
    trade_date: date,
    pre_close: Optional[float] = None,
) -> Tuple[List[dict], List[dict], Optional[time]]:
    """Aggregate 1-min intraday bars into 5-minute OHLC bars.

    Uses each 1-min bar's open/high/low/close. The first bar of the day
    often has open=0 (API quirk); falls back to preClose or current.

    Returns (identity_rows, bar_rows, latest_bar_time).
    """
    if not bars:
        return [], [], None

    # Group 1-min bars by 5-min window end time
    windows: Dict[time, List[dict]] = {}
    for bar in bars:
        if not bar or bar[COL_TIMESTAMP] is None:
            continue

        t = _ms_to_time(bar[COL_TIMESTAMP])
        if t is None:
            continue

        open_v = _to_float(bar[COL_OPEN])
        high_v = _to_float(bar[COL_HIGH])
        low_v = _to_float(bar[COL_LOW])
        close_v = _to_float(bar[COL_CLOSE])
        if close_v is None:
            close_v = _to_float(bar[COL_CURRENT])

        # Skip null rows (non-trading minutes: lunch break, after close)
        if close_v is None:
            continue

        # Fix first bar's open=0 (API quirk): use preClose or current
        if open_v is not None and open_v == 0:
            open_v = pre_close if pre_close else close_v

        # Skip if low is 0 (invalid)
        if low_v is not None and low_v == 0:
            low_v = close_v

        wend = _window_end_5min(t)
        windows.setdefault(wend, []).append({
            "time": t,
            "open": open_v,
            "high": high_v,
            "low": low_v,
            "close": close_v,
        })

    if not windows:
        return [], [], None

    latest_bar_time = max(windows.keys())

    identity_rows: List[dict] = []
    bar_rows: List[dict] = []

    for wend, minute_bars in sorted(windows.items()):
        # Sort by time within the window
        minute_bars.sort(key=lambda b: b["time"])

        o = minute_bars[0]["open"]
        c = minute_bars[-1]["close"]
        highs = [b["high"] for b in minute_bars if b["high"] is not None]
        lows = [b["low"] for b in minute_bars if b["low"] is not None]
        h = max(highs) if highs else None
        low = min(lows) if lows else None

        change = round(c - o, 4) if (c is not None and o is not None) else None
        change_pct = round((c - o) / o * 100, 4) if (c is not None and o is not None and o != 0) else None

        bar_rows.append({
            "date": trade_date,
            "code": code,
            "time": wend,
            "open": o,
            "high": h,
            "low": low,
            "close": c,
            "change": change,
            "change_pct": change_pct,
        })

    identity_rows.append({
        "date": trade_date,
        "code": code,
        "name": name,
    })

    return identity_rows, bar_rows, latest_bar_time


# ---------------------------------------------------------------------------
# DB: upsert bars
# ---------------------------------------------------------------------------

def upsert_index_bars(
    conn,
    identity_rows: List[dict],
    bar_rows: List[dict],
) -> None:
    """Upsert index identity rows (FK parent) then intraday bars."""
    if identity_rows:
        # Deduplicate by (date, code)
        seen = set()
        uniq: List[dict] = []
        for r in identity_rows:
            k = (r["date"], r["code"])
            if k in seen:
                continue
            seen.add(k)
            uniq.append(r)
        bulk_upsert(conn, "stats.index_identity", uniq, ["date", "code"])
    if bar_rows:
        bulk_upsert(conn, "stats.index_intraday_5min", bar_rows, ["date", "code", "time"])


def has_data_for_today(conn, today: date, code: str) -> bool:
    """Check if ``stats.index_intraday_5min`` already has bars for (today, code)."""
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM stats.index_intraday_5min WHERE date = %s AND code = %s LIMIT 1",
                (today, code),
            )
            return cur.fetchone() is not None
    except Exception as e:  # noqa: BLE001
        logger.warning("DB check failed for (%s, %s): %s", today, code, e)
        return False


# ---------------------------------------------------------------------------
# Fetch + process one index code
# ---------------------------------------------------------------------------

def fetch_and_upsert_one(
    session,
    code: str,
    name: str,
    proxy: AntiBotProxy,
    host_tracker: HostStatusTracker,
    conn,
) -> Tuple[int, Optional[time]]:
    """Fetch intraday 1-min bars for one index code from cnindex.com.cn,
    aggregate into 5-min bars, and upsert to DB.

    Returns (n_bars_upserted, latest_bar_time).
    """
    if proxy.is_blocked(CNINDEX_HQ_BASE):
        logger.warning("  [cnindex-stream] %s: cnindex.com.cn is blocked, skipping", code)
        return 0, None

    t0 = _time.time()
    data = fetch_intraday_data(session, code, proxy)
    elapsed = _time.time() - t0

    if data is None:
        logger.info("  [cnindex-stream] %s: NO DATA in %.1fs", code, elapsed)
        return 0, None

    bars = data.get("data") or []
    if not bars:
        logger.info("  [cnindex-stream] %s: no bars available in %.1fs", code, elapsed)
        return 0, None

    # Extract index name from API response
    api_name = data.get("indexName") or name

    # Extract preClose from first row (12th element, index 11)
    pre_close = None
    first_bar = bars[0] if bars else None
    if first_bar and len(first_bar) > COL_PRECLOSE:
        pre_close = _to_float(first_bar[COL_PRECLOSE])

    # Determine trade date from first bar's timestamp
    trade_date = None
    if first_bar and first_bar[COL_TIMESTAMP]:
        trade_date = _ms_to_date(first_bar[COL_TIMESTAMP])
    if trade_date is None:
        trade_date = datetime.now().date()

    identity_rows, bar_rows, latest_time = aggregate_1min_to_5min(
        code, api_name, bars, trade_date, pre_close,
    )

    n_bars = 0
    if bar_rows:
        try:
            upsert_index_bars(conn, identity_rows, bar_rows)
            n_bars = len(bar_rows)
            logger.info(
                "  [cnindex-stream] %s (%s): %d 1min-bars -> %d 5min-bars (latest=%s, preClose=%s) in %.1fs; upserted",
                code, api_name, len(bars), n_bars, latest_time, pre_close, elapsed,
            )
        except Exception as e:
            logger.error("  [cnindex-stream] %s: DB upsert failed: %s", code, e)
    else:
        logger.info(
            "  [cnindex-stream] %s (%s): %d 1min-bars -> 0 5min-bars in %.1fs",
            code, api_name, len(bars), elapsed,
        )

    return n_bars, latest_time


def _fetch_codes(
    session,
    codes: List[str],
    proxy: AntiBotProxy,
    host_tracker: HostStatusTracker,
    conn,
) -> Tuple[int, int, int]:
    """Fetch and upsert intraday data for a list of codes.

    Returns (n_ok, n_fail, n_total_bars).
    """
    n_total_bars = 0
    n_ok = 0
    n_fail = 0
    for i, code in enumerate(codes):
        # Get name from index_identity or use code
        with conn.cursor() as cur:
            cur.execute(
                "SELECT name FROM stats.index_identity WHERE code = %s ORDER BY date DESC LIMIT 1",
                (code,),
            )
            row = cur.fetchone()
            name = row[0] if row else code

        n_bars, _ = fetch_and_upsert_one(
            session, code, name, proxy, host_tracker, conn,
        )
        n_total_bars += n_bars
        if n_bars > 0:
            n_ok += 1
        else:
            n_fail += 1

        # Anti-bot sleep after EVERY fetch (every index must wait, including
        # the last one and even on failure — the proxy's internal sleep is
        # only applied on successful requests, so this guarantees cadence).
        random_sleep(DEFAULT_SLEEP_SEC)

    return n_ok, n_fail, n_total_bars


# ---------------------------------------------------------------------------
# Sleep helpers
# ---------------------------------------------------------------------------

def _sleep_chunked(seconds: float) -> None:
    """Sleep for ``seconds`` in chunks for Ctrl-C responsiveness."""
    if seconds <= 0:
        return
    end = _time.time() + seconds
    while _time.time() < end:
        _time.sleep(min(SLEEP_CHUNK_SEC, max(0.0, end - _time.time())))


def _sleep_until(target: time, label: str = "") -> None:
    """Sleep until ``target`` time today. Returns immediately if already past."""
    now = datetime.now()
    target_dt = datetime.combine(now.date(), target)
    if now >= target_dt:
        return
    wait_sec = (target_dt - now).total_seconds()
    logger.info("Sleeping %.0fs until %s %s", wait_sec, target, label)
    _sleep_chunked(wait_sec)


def _sleep_until_next_day() -> None:
    """Sleep until tomorrow's 00:00 so the main loop re-enters on a new day."""
    now = datetime.now()
    tomorrow_midnight = datetime.combine(now.date() + timedelta(days=1), time(0, 0))
    wait_sec = (tomorrow_midnight - now).total_seconds()
    logger.info("All fetches done for today; sleeping %.0fs until next day.", wait_sec)
    _sleep_chunked(wait_sec)


# ---------------------------------------------------------------------------
# Main stream loop
# ---------------------------------------------------------------------------

def stream(
    once: bool = False,
    single_code: Optional[str] = None,
    index_codes: Optional[List[str]] = None,
) -> None:
    """Main streaming loop with two-time-point scheduling.

    Per trading day:
      - Before 16:00: sleep and wait.
      - 16:00–16:30 (1st window): check DB for existing data per code; if
        no data, fetch once per index. Then sleep until 16:30.
      - After 16:30 (2nd window): fetch once per index. Then sleep until
        next day.

    ``--once`` bypasses scheduling and does an immediate fetch of all codes.
    """
    if index_codes is None:
        index_codes = list(CNINDEX_CODES)

    logger.info(
        "[startup] cnindex streamer starting "
        "(windows: %s-%s & after %s, sleep=%.0fs, codes=%s)",
        FIRST_WINDOW_START, SECOND_WINDOW_START, SECOND_WINDOW_START,
        DEFAULT_SLEEP_SEC, index_codes,
    )

    conn = get_db_connection()
    session = build_default_session(merge_browser_profile(CNINDEX_HEADERS))
    host_tracker = HostStatusTracker()
    proxy = AntiBotProxy(AntiBotConfig(base_sleep_sec=DEFAULT_SLEEP_SEC))

    # Per-day state: reset at the start of each new day.
    current_biz_day: Optional[date] = None
    first_fetch_done = False   # 1st window (16:00–16:30) fetch completed
    second_fetch_done = False  # 2nd window (after 16:30) fetch completed

    try:
        while True:
            today = datetime.now().date()
            now_time = datetime.now().time()
            trading_today = is_trading_day(today)

            # --- Single-code mode (--code) ---
            if single_code:
                logger.info("=== single-code mode: %s ===", single_code)
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT name FROM stats.index_identity WHERE code = %s ORDER BY date DESC LIMIT 1",
                        (single_code,),
                    )
                    row = cur.fetchone()
                    name = row[0] if row else single_code
                fetch_and_upsert_one(session, single_code, name, proxy, host_tracker, conn)
                break

            # --- --once mode: immediate fetch of all codes, then exit ---
            if once:
                logger.info("=== --once mode: immediate fetch of all codes ===")
                n_ok, n_fail, n_bars = _fetch_codes(
                    session, index_codes, proxy, host_tracker, conn,
                )
                logger.info(
                    "=== --once done: %d/%d ok, %d failed, %d bars ===",
                    n_ok, len(index_codes), n_fail, n_bars,
                )
                break

            # --- Reset per-day state on a new day ---
            if current_biz_day != today:
                current_biz_day = today
                first_fetch_done = False
                second_fetch_done = False
                logger.info("New biz day %s; state reset.", today)

            # --- Not a trading day: sleep and wait ---
            if not trading_today:
                logger.info("Today (%s) is not a trading day; sleeping %ds.",
                            today, NON_TRADING_SLEEP_SEC)
                _sleep_chunked(NON_TRADING_SLEEP_SEC)
                continue

            # --- Before 16:00: sleep until 1st window ---
            if now_time < FIRST_WINDOW_START:
                _sleep_until(FIRST_WINDOW_START, "(1st fetch window)")
                continue

            # --- 1st window (16:00 – 16:30): check DB, fetch missing ---
            if now_time < SECOND_WINDOW_START:
                if not first_fetch_done:
                    logger.info("=== 1st window @ %s: checking DB for existing data ===",
                                datetime.now().strftime("%H:%M:%S"))
                    codes_to_fetch = [
                        c for c in index_codes
                        if not has_data_for_today(conn, today, c)
                    ]
                    if codes_to_fetch:
                        logger.info(
                            "1st window: %d/%d codes missing data, fetching: %s",
                            len(codes_to_fetch), len(index_codes), codes_to_fetch,
                        )
                        n_ok, n_fail, n_bars = _fetch_codes(
                            session, codes_to_fetch, proxy, host_tracker, conn,
                        )
                        logger.info(
                            "=== 1st window done: %d/%d ok, %d failed, %d bars ===",
                            n_ok, len(codes_to_fetch), n_fail, n_bars,
                        )
                    else:
                        logger.info("1st window: all codes already have data for today; skipping fetch.")
                    first_fetch_done = True
                else:
                    logger.info("1st window already done; waiting until %s.", SECOND_WINDOW_START)

                # Sleep until 2nd window
                _sleep_until(SECOND_WINDOW_START, "(2nd fetch window)")
                continue

            # --- 2nd window (after 16:30): fetch all codes ---
            if not second_fetch_done:
                logger.info("=== 2nd window @ %s: fetching all codes (final) ===",
                            datetime.now().strftime("%H:%M:%S"))
                n_ok, n_fail, n_bars = _fetch_codes(
                    session, index_codes, proxy, host_tracker, conn,
                )
                logger.info(
                    "=== 2nd window done: %d/%d ok, %d failed, %d bars ===",
                    n_ok, len(index_codes), n_fail, n_bars,
                )
                second_fetch_done = True
            else:
                logger.info("2nd window already done; waiting until next day.")

            # Sleep until next day (loop will re-enter on new day and reset state)
            _sleep_until_next_day()

    except KeyboardInterrupt:
        logger.info("Interrupted by user; exiting.")
    finally:
        conn.close()
        session.close()
        logger.info("Cleanup done.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Stream CNINDEX intraday data for CNINDEX-published indices.",
    )
    parser.add_argument(
        "--once", action="store_true",
        help="Bypass scheduling: do one immediate fetch of all codes then exit.",
    )
    parser.add_argument(
        "--code", type=str, default=None,
        help="Fetch a single index code then exit (e.g. --code 399303).",
    )
    args = parser.parse_args()
    stream(once=args.once, single_code=args.code)


if __name__ == "__main__":
    main()
