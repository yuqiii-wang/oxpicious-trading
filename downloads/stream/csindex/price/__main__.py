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
  4. Archives bars to CSV (temps/csindex_intraday/) BEFORE DB upsert so
     DB failures don't lose data.
  5. Upserts bars into ``stats.index_intraday_5min`` (+ ``stats.index_identity``).

CSV backfill
------------
At startup and every 5 minutes outside trading hours, the streamer scans
``temps/csindex_intraday/`` for archived CSV files and upserts them to DB.
This recovers data lost to DB connection failures mid-sweep. Idempotent
via ON CONFLICT.

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
import sys
import time as _time
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

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
    random_sleep,
    setup_logger,
)
from downloads.index.csindex.quote import CSINDEX_BASE, CSINDEX_HEADERS
from _common.db_commons import get_db_connection
from _common._holidays_and_weekdays import is_trading_day

from ._constants import (
    BACKFILL_INTERVAL_SEC,
    CSINDEX_START_TIME,
    LOOP_INTERVAL_SEC,
)
from ._csv_io import backfill_csvs
from ._db import (
    load_index_industry_map,
    load_missing_or_stale_codes,
    load_sse_streamed_codes,
    order_codes_by_industry_coverage,
)
from ._fetch import fetch_and_upsert_one

logger = setup_logger("csindex_stream")


def _seconds_until_next_loop(last_loop_start: float) -> float:
    """Seconds to sleep until the next 30-min loop boundary."""
    elapsed = _time.time() - last_loop_start
    remaining = LOOP_INTERVAL_SEC - elapsed
    return max(0.0, remaining)


def _sleep_chunks(seconds: float, chunk: float = 5.0) -> None:
    """Sleep in chunks for Ctrl-C responsiveness."""
    end = _time.time() + seconds
    while _time.time() < end:
        _time.sleep(min(chunk, max(0.0, end - _time.time())))


def stream(once: bool = False, single_code: Optional[str] = None) -> None:
    """Main streaming loop.

    Each iteration:
      1. Find missing/stale index codes from DB (EXCLUDING codes already
         streamed by SSE — CSIndex only downloads indices SSE does NOT cover).
      2. Fetch intraday ticks for each via csindex API (with antibot sleep).
      3. Aggregate to 5-min bars, archive to CSV, upsert to DB.
      4. Sleep until next 30-min boundary.

    SSE head start: On each new trading day, CSIndex waits until
    ``CSINDEX_START_TIME`` (09:40, 10 min after SSE's 09:30 open) before
    its first loop. This gives SSE time to produce its first bars, so
    CSIndex can query which codes SSE is streaming and exclude them.

    CSV backfill: At startup and every 5 minutes outside trading hours,
    archived CSVs are loaded to DB to recover data lost to DB failures.
    """
    logger.info(
        "[startup] csindex streamer starting (loop_interval=%ds, sleep=%.0fs, "
        "sse_head_start -> csindex_start=%s)",
        LOOP_INTERVAL_SEC, DEFAULT_SLEEP_SEC, CSINDEX_START_TIME,
    )

    conn = get_db_connection()
    session = build_default_session(merge_browser_profile(CSINDEX_HEADERS))
    host_tracker = HostStatusTracker()
    proxy = AntiBotProxy(AntiBotConfig(base_sleep_sec=DEFAULT_SLEEP_SEC))

    # --- Startup CSV backfill: load any CSV data not yet in DB ---
    t0 = _time.time()
    backfill_csvs(conn)
    logger.info("[startup] CSV backfill done in %.1fs.", _time.time() - t0)

    # Per-biz-day state: refreshed at the start of each new trading day
    current_biz_day = None
    sse_streamed_codes: set = set()
    index_industry_map: Dict[str, str] = {}

    try:
        while True:
            loop_start = _time.time()
            today = datetime.now().date()
            now_time = datetime.now().time()
            trading_today = is_trading_day(today)

            # --- Single-code mode (--code) ---
            if single_code:
                logger.info("=== single-code mode: %s ===", single_code)
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT COALESCE(name, %s) FROM stats.sec_classification "
                        "WHERE code=%s AND type='index' LIMIT 1",
                        (single_code, single_code),
                    )
                    row = cur.fetchone()
                    name = row[0] if row else single_code
                fetch_and_upsert_one(session, single_code, name, proxy, host_tracker, conn)
                break

            # --- Non-trading day: backfill CSV every 5 min, sleep, repeat ---
            if not trading_today:
                backfill_csvs(conn)
                logger.info(
                    "Non-trading day (%s); backfill done; sleeping %ds.",
                    today, BACKFILL_INTERVAL_SEC,
                )
                if once:
                    break
                _sleep_chunks(BACKFILL_INTERVAL_SEC)
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
                    _sleep_chunks(wait_sec)

                current_biz_day = today
                sse_streamed_codes = load_sse_streamed_codes(conn, today)
                index_industry_map = load_index_industry_map(conn)
                logger.info(
                    "Anchored to biz day %s: %d codes already streamed by SSE/SZSE "
                    "(excluded from CSIndex download); loaded index→industry map "
                    "(%d codes across %d industries).",
                    today, len(sse_streamed_codes),
                    len(index_industry_map),
                    len(set(index_industry_map.values())),
                )

            # Find missing/stale codes, EXCLUDING SSE-streamed codes.
            codes = load_missing_or_stale_codes(
                conn, today, exclude_codes=sse_streamed_codes,
            )
            ordered_codes = order_codes_by_industry_coverage(
                codes, index_industry_map,
            )
            n_industries = (
                len({index_industry_map.get(c, "OTHER") for c, _ in ordered_codes})
                if ordered_codes else 0
            )
            n_head = min(len(ordered_codes), n_industries)
            logger.info(
                "=== loop @ %s biz=%s: %d codes to fetch across %d industries "
                "(trading_hours=%s, excluded=%d sse-streamed); "
                "industry-first: head=%d + tail=%d ===",
                datetime.now().strftime("%H:%M:%S"), today, len(ordered_codes),
                n_industries,
                CSINDEX_START_TIME <= now_time,  # rough trading-hours flag
                len(sse_streamed_codes),
                n_head, len(ordered_codes) - n_head,
            )

            if not ordered_codes:
                logger.info("No missing/stale codes; all indices up to date.")
            else:
                n_total_bars = 0
                n_ok = 0
                n_fail = 0
                for i, (code, name) in enumerate(ordered_codes):
                    if proxy.is_blocked(CSINDEX_BASE):
                        logger.warning(
                            "csindex.com.cn blocked; stopping this loop (%d/%d done)",
                            i, len(ordered_codes),
                        )
                        break

                    n_bars, _ = fetch_and_upsert_one(
                        session, code, name, proxy, host_tracker, conn,
                    )
                    n_total_bars += n_bars
                    if n_bars > 0:
                        n_ok += 1
                    else:
                        n_fail += 1

                    if i < len(ordered_codes) - 1:
                        random_sleep(DEFAULT_SLEEP_SEC)

                loop_elapsed = _time.time() - loop_start
                logger.info(
                    "=== loop done: %d/%d codes fetched, %d failed, %d total bars in %.1fs ===",
                    n_ok, len(ordered_codes), n_fail, n_total_bars, loop_elapsed,
                )

            if once:
                logger.info("--once set; exiting after one loop.")
                break

            # Sleep until next 30-min boundary
            sleep_sec = _seconds_until_next_loop(loop_start)
            if sleep_sec > 0:
                logger.info("Sleeping %.0fs until next loop.", sleep_sec)
                _sleep_chunks(sleep_sec)
            else:
                logger.info("Loop took longer than %ds; starting next loop immediately.",
                            LOOP_INTERVAL_SEC)

    except KeyboardInterrupt:
        logger.info("Interrupted by user; exiting.")
    finally:
        conn.close()
        session.close()
        logger.info("Cleanup done.")


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
