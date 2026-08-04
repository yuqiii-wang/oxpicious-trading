"""Stream CSIndex intraday data for indices missing from stats.index_intraday_5min.

This is a "gap-filler" streamer that complements the SSE and SZSE live
streamers. It uses the csindex.com.cn ``index-perf-oneday`` API (the same
endpoint behind https://www.csindex.com.cn/#/indices/family/detail?indexCode=930641)
to fetch intraday ticks for index codes that are missing or stale in
``stats.index_intraday_5min``.

SSE head start + exclusion
--------------------------
CSIndex starts 10 minutes after SSE (``CSINDEX_START_TIME`` = 09:40, while
SSE starts at 09:30). At the start of each trading day — AFTER the 10-min
delay — CSIndex queries ``stats.index_intraday_5min`` for codes that already
have bars today. Those are the indices SSE (and SZSE) is actively streaming.
CSIndex excludes them from its download list, so it only fetches indices
that SSE does NOT cover (typically 930xxx/931xxx CSIndex-published indices).

Loop cadence: 30 minutes. Each loop:
  1. Finds index codes in ``index_basic_stats`` (latest date) that are either:
     - Missing entirely from ``index_intraday_5min`` for today, OR
     - Stale (latest bar time > 30 min behind current time during trading hours)
     ...EXCLUDING codes already streamed by SSE (codes with bars today).
  2. Fetches intraday ticks (~15s granularity) for each code via csindex API.
  3. Aggregates ticks into 5-minute OHLC bars (ceiling convention, same as
     SSE/SZSE — see ``_window_end_5min``).
  4. Upserts bars into ``stats.index_intraday_5min`` (+ ``stats.index_identity``).

Anti-bot: reuses ``AntiBotProxy`` with ``DEFAULT_SLEEP_SEC`` (20s jittered)
per request — same pattern as the SSE/SZSE streamers and the existing
``downloads.index.csindex.quote`` module.

Usage:
  python -m downloads.stream.csindex.price              # stream (30-min loops)
  python -m downloads.stream.csindex.price --once       # one loop then exit
  python -m downloads.stream.csindex.price --code 930641 # fetch one code
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

from downloads._common.core import (
    DEFAULT_SLEEP_SEC,
    AntiBotConfig,
    AntiBotProxy,
    HostStatusTracker,
    build_default_session,
    merge_browser_profile,
    setup_logger,
    random_sleep,
)
from downloads.index.csindex.quote.__main__ import (
    CSINDEX_BASE,
    CSINDEX_HEADERS,
    CSINDEX_SKIP_CODES,
    fetch_intraday,
)
from utils.db_commons import bulk_upsert, get_db_connection
from utils._holidays_and_weekdays import is_trading_day

logger = setup_logger("csindex_stream")

# Loop cadence: 30 minutes between full sweeps.
LOOP_INTERVAL_SEC = 30 * 60  # 1800s

# A code is "stale" if its latest intraday bar is older than this many minutes
# behind the current time (during trading hours). Triggers a re-fetch.
STALE_THRESHOLD_MIN = 30

# Trading hours for stale-checking (don't re-fetch outside trading hours
# unless completely missing).
TRADING_START = time(9, 25)
TRADING_END = time(15, 5)

# CSIndex starts 10 minutes after SSE so SSE has already produced its first
# 2 bars (09:35, 09:40) by the time CSIndex queries which codes SSE is
# streaming. CSIndex then excludes those codes from its download list,
# downloading only indices that SSE does NOT cover.
SSE_HEAD_START_MIN = 10
CSINDEX_START_TIME = time(9, 30 + SSE_HEAD_START_MIN)  # 09:40


# ---------------------------------------------------------------------------
# DB: load SSE-streamed codes (codes with bars today — exclude from CSIndex)
# ---------------------------------------------------------------------------

def load_sse_streamed_codes(conn, today: date) -> set:
    """Return the set of index codes that already have bars in
    ``stats.index_intraday_5min`` for ``today``.

    These are codes being actively streamed by SSE (and SZSE). CSIndex
    excludes them from its download list to avoid redundant fetches —
    CSIndex only downloads indices that SSE does NOT cover.

    Called at the start of each trading day, AFTER the 10-minute SSE head
    start, so SSE has already written its first bars.
    """
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT code FROM stats.index_intraday_5min WHERE date = %s",
                (today,),
            )
            return {r[0] for r in cur.fetchall()}
    except Exception as e:  # noqa: BLE001
        logger.warning("Failed to load SSE-streamed codes for %s: %s", today, e)
        return set()


# ---------------------------------------------------------------------------
# DB: find missing / stale index codes
# ---------------------------------------------------------------------------

def load_missing_or_stale_codes(
    conn, today: date, exclude_codes: Optional[set] = None,
) -> List[Tuple[str, str]]:
    """Return ``[(code, name), ...]`` for indices that are either missing
    from ``index_intraday_5min`` for ``today`` or have stale bars.

    "Missing" = code exists in ``index_basic_stats`` (latest date) but has
    no rows in ``index_intraday_5min`` for ``today``.

    "Stale" = latest bar time in ``index_intraday_5min`` for ``today`` is
    more than ``STALE_THRESHOLD_MIN`` minutes behind current time, and we
    are within trading hours.

    Args:
        exclude_codes: set of codes to EXCLUDE from the result (codes being
            streamed by SSE/SZSE). CSIndex only downloads indices that SSE
            does NOT cover, so SSE-streamed codes are excluded.
    """
    now = datetime.now()
    now_time = now.time()
    in_trading = TRADING_START <= now_time <= TRADING_END

    if in_trading:
        # Missing OR stale: latest bar < now - 30 min
        stale_cutoff = (now - timedelta(minutes=STALE_THRESHOLD_MIN)).time()
        query = """
            WITH latest_stats AS (
                SELECT code, MAX(date) AS max_date
                  FROM stats.index_basic_stats
                 GROUP BY code
            ),
            today_bars AS (
                SELECT code, MAX(time) AS latest_time
                  FROM stats.index_intraday_5min
                 WHERE date = %s
                 GROUP BY code
            )
            SELECT DISTINCT ls.code, COALESCE(sc.name, ls.code) AS name
              FROM latest_stats ls
              LEFT JOIN today_bars tb ON tb.code = ls.code
              LEFT JOIN stats.sec_classification sc
                ON sc.code = ls.code AND sc.type = 'index'
             WHERE tb.code IS NULL              -- missing entirely
                OR tb.latest_time < %s          -- stale (latest bar too old)
             ORDER BY ls.code
        """
        with conn.cursor() as cur:
            cur.execute(query, (today, stale_cutoff))
            rows = cur.fetchall()
    else:
        # Outside trading hours: only fetch codes that are completely missing
        # for today (no stale check — market is closed).
        query = """
            WITH latest_stats AS (
                SELECT code, MAX(date) AS max_date
                  FROM stats.index_basic_stats
                 GROUP BY code
            ),
            today_bars AS (
                SELECT code
                  FROM stats.index_intraday_5min
                 WHERE date = %s
                 GROUP BY code
            )
            SELECT DISTINCT ls.code, COALESCE(sc.name, ls.code) AS name
              FROM latest_stats ls
              LEFT JOIN today_bars tb ON tb.code = ls.code
              LEFT JOIN stats.sec_classification sc
                ON sc.code = ls.code AND sc.type = 'index'
             WHERE tb.code IS NULL
             ORDER BY ls.code
        """
        with conn.cursor() as cur:
            cur.execute(query, (today,))
            rows = cur.fetchall()

    # Filter out CSINDEX_SKIP_CODES (handled by SZSE streamer) and
    # exclude_codes (handled by SSE streamer — CSIndex only downloads
    # indices that SSE does NOT cover).
    excluded = CSINDEX_SKIP_CODES | (exclude_codes or set())
    result = [(r[0], r[1]) for r in rows if r[0] not in excluded]
    return result


# ---------------------------------------------------------------------------
# Aggregation: ~15s ticks -> 5-min OHLC bars
# ---------------------------------------------------------------------------

def _parse_trade_time(time_str: str) -> Optional[time]:
    """Parse 'HH:MM:SS' or 'HH:MM' into a datetime.time."""
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            return datetime.strptime(time_str.strip(), fmt).time()
        except ValueError:
            continue
    return None


def _window_end_5min(t: time) -> time:
    """Return the 5-minute window END time for a given tick time (ceiling).

    Uses the SAME ceiling convention as SSE (ceiling_5min) and SZSE
    (_window_end_minute): a tick at 09:35 closes the 09:35 bar (NOT 09:40).
    This keeps all three streamers on the identical 5-min grid
    (09:35, 09:40, ..., 15:00).

    09:30 -> 09:30, 09:31-09:35 -> 09:35, 09:36-09:40 -> 09:40, ...
    """
    minute = t.hour * 60 + t.minute
    wend_minute = ((minute - 1) // 5 + 1) * 5
    h, m = divmod(wend_minute, 60)
    return time(h, m)


def aggregate_ticks_to_5min(
    code: str,
    name: str,
    ticks: List[Dict[str, Any]],
    trade_date: date,
) -> Tuple[List[dict], List[dict], Optional[time]]:
    """Aggregate ~15s intraday ticks into 5-minute OHLC bars.

    Uses the ``current`` field (real-time price at each tick) for OHLC.
    The API's ``high``/``low`` fields are cumulative day running values
    (NOT per-tick), so they are NOT used for bar high/low.

    Returns (identity_rows, bar_rows, latest_bar_time).
    """
    if not ticks:
        return [], [], None

    # Group ticks by 5-min window end time
    windows: Dict[time, List[float]] = {}
    for tick in ticks:
        time_str = tick.get("tradeTime") or ""
        t = _parse_trade_time(str(time_str))
        if t is None:
            continue
        try:
            price = float(tick.get("current") or 0)
        except (ValueError, TypeError):
            continue
        if price <= 0:
            continue
        wend = _window_end_5min(t)
        windows.setdefault(wend, []).append(price)

    if not windows:
        return [], [], None

    latest_bar_time = max(windows.keys())

    identity_rows: List[dict] = []
    bar_rows: List[dict] = []
    for wend, prices in sorted(windows.items()):
        o = prices[0]
        h = max(prices)
        low = min(prices)
        c = prices[-1]
        change = round(c - o, 4)
        change_pct = round((c - o) / o * 100, 4) if o else None

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
    """Fetch intraday ticks for one index code from csindex.com.cn,
    aggregate into 5-min bars, and upsert to DB.

    Returns (n_bars_upserted, latest_bar_time).
    """
    if proxy.is_blocked(CSINDEX_BASE):
        logger.warning("  [csindex-stream] %s: csindex.com.cn is blocked, skipping", code)
        return 0, None

    t0 = _time.time()
    data = fetch_intraday(session, code, proxy)
    elapsed = _time.time() - t0

    if data is None:
        logger.info("  [csindex-stream] %s: NO DATA in %.1fs", code, elapsed)
        return 0, None

    header = data.get("intraDayHeader") or {}
    tick_list = data.get("intraDayPerfList") or []
    if not tick_list:
        logger.info("  [csindex-stream] %s: no ticks available in %.1fs", code, elapsed)
        return 0, None

    # Parse trade date from header or first tick
    trade_date_raw = (header.get("tradeDate") or "").strip()
    if not trade_date_raw and tick_list:
        trade_date_raw = str(tick_list[0].get("tradeDate") or "").strip()
    try:
        trade_date = datetime.strptime(trade_date_raw[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        trade_date = datetime.now().date()

    # Get index name from tick data (more descriptive than sec_classification)
    tick_name = ""
    if tick_list:
        tick_name = tick_list[0].get("indexName") or ""
    if not tick_name:
        tick_name = name

    identity_rows, bar_rows, latest_time = aggregate_ticks_to_5min(
        code, tick_name, tick_list, trade_date,
    )

    n_bars = 0
    if bar_rows:
        try:
            upsert_index_bars(conn, identity_rows, bar_rows)
            n_bars = len(bar_rows)
            logger.info(
                "  [csindex-stream] %s (%s): %d ticks -> %d bars (latest=%s) in %.1fs; upserted",
                code, tick_name, len(tick_list), n_bars, latest_time, elapsed,
            )
        except Exception as e:
            logger.error("  [csindex-stream] %s: DB upsert failed: %s", code, e)
    else:
        logger.info(
            "  [csindex-stream] %s (%s): %d ticks -> 0 bars in %.1fs",
            code, tick_name, len(tick_list), elapsed,
        )

    return n_bars, latest_time


# ---------------------------------------------------------------------------
# Main stream loop
# ---------------------------------------------------------------------------

def _seconds_until_next_loop(last_loop_start: float) -> float:
    """Seconds to sleep until the next 30-min loop boundary."""
    elapsed = _time.time() - last_loop_start
    remaining = LOOP_INTERVAL_SEC - elapsed
    return max(0.0, remaining)


def stream(once: bool = False, single_code: Optional[str] = None) -> None:
    """Main streaming loop.

    Each iteration:
      1. Find missing/stale index codes from DB (EXCLUDING codes already
         streamed by SSE — CSIndex only downloads indices SSE does NOT cover).
      2. Fetch intraday ticks for each via csindex API (with antibot sleep).
      3. Aggregate to 5-min bars and upsert to DB.
      4. Sleep until next 30-min boundary.

    SSE head start: On each new trading day, CSIndex waits until
    ``CSINDEX_START_TIME`` (09:40, 10 min after SSE's 09:30 open) before
    its first loop. This gives SSE time to produce its first bars, so
    CSIndex can query which codes SSE is streaming and exclude them.
    """
    logger.info("[startup] csindex streamer starting (loop_interval=%ds, sleep=%.0fs, "
                "sse_head_start=%dmin -> csindex_start=%s)",
                LOOP_INTERVAL_SEC, DEFAULT_SLEEP_SEC,
                SSE_HEAD_START_MIN, CSINDEX_START_TIME)

    conn = get_db_connection()
    session = build_default_session(merge_browser_profile(CSINDEX_HEADERS))
    host_tracker = HostStatusTracker()
    proxy = AntiBotProxy(AntiBotConfig(base_sleep_sec=DEFAULT_SLEEP_SEC))

    # Per-biz-day state: refreshed at the start of each new trading day
    # (after the 10-min SSE head start).
    current_biz_day = None
    sse_streamed_codes: set = set()

    try:
        while True:
            loop_start = _time.time()
            today = datetime.now().date()
            now_time = datetime.now().time()
            trading_today = is_trading_day(today)

            # --- Single-code mode (--code) ---
            if single_code:
                logger.info("=== single-code mode: %s ===", single_code)
                # Get name from sec_classification
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT COALESCE(name, %s) FROM stats.sec_classification WHERE code=%s AND type='index' LIMIT 1",
                        (single_code, single_code),
                    )
                    row = cur.fetchone()
                    name = row[0] if row else single_code
                fetch_and_upsert_one(session, single_code, name, proxy, host_tracker, conn)
                break

            # --- Normal loop mode ---
            if not trading_today:
                logger.info("Today (%s) is not a trading day; sleeping %ds.",
                            today, LOOP_INTERVAL_SEC)
                if once:
                    break
                random_sleep(LOOP_INTERVAL_SEC)
                continue

            # --- New trading day: wait for SSE head start, then gather
            #     which codes SSE is streaming (codes with bars today). ---
            if current_biz_day != today:
                now_dt = datetime.now()
                start_dt = datetime.combine(today, CSINDEX_START_TIME)
                if now_dt < start_dt:
                    wait_sec = (start_dt - now_dt).total_seconds()
                    logger.info(
                        "New trading day %s; waiting %.0fs until %s "
                        "(SSE head start so it produces first bars).",
                        today, wait_sec, CSINDEX_START_TIME,
                    )
                    # Sleep in chunks for Ctrl-C responsiveness.
                    end = _time.time() + wait_sec
                    while _time.time() < end:
                        _time.sleep(min(5.0, max(0.0, end - _time.time())))

                # SSE has now produced at least its first bar (09:35).
                # Query which codes have bars today — those are SSE-streamed.
                current_biz_day = today
                sse_streamed_codes = load_sse_streamed_codes(conn, today)
                logger.info(
                    "Anchored to biz day %s: %d codes already streamed by SSE/SZSE "
                    "(excluded from CSIndex download).",
                    today, len(sse_streamed_codes),
                )

            # Find missing/stale codes, EXCLUDING SSE-streamed codes.
            codes = load_missing_or_stale_codes(
                conn, today, exclude_codes=sse_streamed_codes,
            )
            logger.info(
                "=== loop @ %s biz=%s: %d codes to fetch (trading_hours=%s, "
                "excluded=%d sse-streamed) ===",
                datetime.now().strftime("%H:%M:%S"), today, len(codes),
                TRADING_START <= now_time <= TRADING_END,
                len(sse_streamed_codes),
            )

            if not codes:
                logger.info("No missing/stale codes; all indices up to date.")
            else:
                n_total_bars = 0
                n_ok = 0
                n_fail = 0
                for i, (code, name) in enumerate(codes):
                    if proxy.is_blocked(CSINDEX_BASE):
                        logger.warning("csindex.com.cn blocked; stopping this loop (%d/%d done)",
                                       i, len(codes))
                        break

                    n_bars, _ = fetch_and_upsert_one(
                        session, code, name, proxy, host_tracker, conn,
                    )
                    n_total_bars += n_bars
                    if n_bars > 0:
                        n_ok += 1
                    else:
                        n_fail += 1

                    # Anti-bot sleep between fetches (already applied by proxy
                    # inside fetch_intraday, but add explicit sleep for
                    # consistency with SSE/SZSE streamers).
                    if i < len(codes) - 1:
                        random_sleep(DEFAULT_SLEEP_SEC)

                loop_elapsed = _time.time() - loop_start
                logger.info(
                    "=== loop done: %d/%d codes fetched, %d failed, %d total bars in %.1fs ===",
                    n_ok, len(codes), n_fail, n_total_bars, loop_elapsed,
                )

            if once:
                logger.info("--once set; exiting after one loop.")
                break

            # Sleep until next 30-min boundary
            sleep_sec = _seconds_until_next_loop(loop_start)
            if sleep_sec > 0:
                logger.info("Sleeping %.0fs until next loop.", sleep_sec)
                # Sleep in chunks for Ctrl-C responsiveness
                end = _time.time() + sleep_sec
                while _time.time() < end:
                    _time.sleep(min(5.0, max(0.0, end - _time.time())))
            else:
                logger.info("Loop took longer than %ds; starting next loop immediately.",
                            LOOP_INTERVAL_SEC)

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
        description="Stream CSIndex intraday data for missing indices.",
    )
    parser.add_argument(
        "--once", action="store_true",
        help="Run one loop then exit (default: loop forever).",
    )
    parser.add_argument(
        "--code", type=str, default=None,
        help="Fetch a single index code then exit (e.g. --code 930641).",
    )
    args = parser.parse_args()
    stream(once=args.once, single_code=args.code)


if __name__ == "__main__":
    main()
