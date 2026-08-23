"""Stream SZSE equity AND index prices into 5-min OHLCV bars.

Two operating modes, switched by wall-clock time on trading days:

  * HOURLY MODE (before 15:30): Streams 2 always-on SZSE indices
    (399001 深证成指, 399006 创业板指) PLUS yesterday's
    top-500 traded SZSE stocks (by trading_amount from stock_basic_stats).
    One round per hour (sleeps until the next HH:00 boundary, or until the
    next trading session if all stocks have already reached CLOSE_TIME).
    Indices go to stats.index_intraday_5min; stocks go to stats.stock_intraday_5min.

  * FULL MODE (at/after 15:30): Streams the full ETF-member stock list
    (load_target_stocks — all SZSE stocks with is_in_index_or_etf=TRUE),
    looping rounds until every stock reaches CLOSE_TIME (15:00). This is
    the original end-of-day sweep behavior.

Afternoon-only start: the streamer does NOT run during the morning session.
On trading days it waits until 13:30 before entering the main loop, so all
API calls are focused on the afternoon trading session (13:30–15:00).

Architecture (see ``_workers.py`` for the async machinery):
  * Round-based streaming. The target list is split into N groups placed on
    a shared asyncio.Queue.
  * FOUR parallel async worker procs pull from the queue concurrently:
      proc A (akshare)     — AkShare ``ak.stock_zh_a_minute`` (Sina 1-min).
      proc C (em_push2his) — East Money push2his trends2 (ndays=5).
      proc D (em_push2)    — East Money push2 trends2 (ndays=1, iscr=1;
                             a DIFFERENT host from C).
      proc B (szse)        — SZSE ``/api/market/ssjjhq/getTimeData``.
    A, C, D all pull from the primary queue; B drains the fallback queue first
    then the primary. Whoever finishes a group first takes the next group
    (dynamic dispatch). Each worker applies its own anti-bot cooldown between
    fetches; the four hit different hosts (Sina, push2his.eastmoney.com,
    push2.eastmoney.com, szse.cn) so they don't contend on rate limits.
    Crucially only proc A ever touches V8, so the old
    partition_address_space.cc(243) race cannot happen.
  * Index streaming (hourly mode only): the 3 always-on indices are fetched
    sequentially via the SZSE ssjjhq API (same endpoint as proc B, but the
    index picupdata has a 6-field layout vs the stock 7-field layout).
    Index bars have NO volume column (index_intraday_5min schema).
  * Error handoff: if proc A/C/D fails on a stock (4xx / timeout) it returns
    the not-yet-finished remainder of that group to a fallback queue, which
    proc B resumes with the SZSE source. A hard exception in any worker
    requeues the unfinished group for B to resume.
  * No-advance backoff: if a full-mode round completes with no stock advancing
    (pre-open or lunch break), the loop backs off instead of hammering the API.
  * Each fetch runs in a worker thread with a hard FETCH_TIMEOUT_SEC (120s)
    timeout — a hung fetch that downloads nothing for 2 min is abandoned and
    logged, never stalling the stream.
  * 1-minute samples are aggregated into 5-minute OHLCV bars:
      open       = first minute's close (last price) in the window
      high       = max of all minute closes
      low        = min of all minute closes
      close      = last minute's close
      volume     = sum of per-minute volumes across the window (stocks only)
      change     = close - open
      change_pct = (close - open) / open * 100
  * Bars are archived to CSV (temps/szse_intraday/) and upserted into
    stats.stock_intraday_5min (FK parent stats.stock_identity) or
    stats.index_intraday_5min (FK parent stats.index_identity).

Anti-bot: reuses safe_get(), build_headers_with_referer(), random_sleep,
HostStatusTracker and build_default_session() from _download_commons.py.
Cooldown: random_sleep(DEFAULT_SLEEP_SEC) per worker between fetches
(jittered [10,30]s); NO_ADVANCE_BACKOFF_SEC (60s) when no stock advanced.

Termination: Ctrl-C cancels both async workers (state.stop + task.cancel) and
exits cleanly after reaping them; the finally block closes the DB and session.

Note: CSV backfill (recovering missed data from archived CSV files) is
handled by the download/archive modules, NOT by this streaming module.

Requires tables from database/sql/05_index_baseline.sql (index_identity +
index_intraday_5min) and database/sql/06_stock_baseline.sql (stock_identity +
stock_intraday_5min) and database/sql/03_sec_composition.sql (sec_composition).

Usage:
  python -m downloads.stream.szse.price              # stream (hourly + full modes)
  python -m downloads.stream.szse.price --once       # one round then exit
  python -m downloads.stream.szse.price --groups 5   # 5 groups (sequential, dev)
  python -m downloads.stream.szse.price --interval 1 # 1s cooldown between fetches (dev)
"""
from __future__ import annotations

import argparse
import asyncio
import locale as _locale
import sys
import time as _time
from datetime import datetime, time, timedelta

from downloads._common.core import (
    DEFAULT_SLEEP_SEC,
    HostStatusTracker,
    build_default_session,
    is_trading_day,
    setup_logger,
)
from _common.db_commons import get_db_connection
from _common.study_and_select_stocks import (
    load_target_stocks,
    load_yesterday_top_traded_stocks,
)

from ._akshare_source import _get_akshare
from ._em_source import _get_em_session
from ._io import write_cycle_csv
from ._workers import (
    CLOSE_TIME,
    _parallel_source_worker,
    _run_index_round,
    _seconds_until_next_hour,
    _szse_worker,
    _not_finished,
    async_sleep_chunks,
    async_sleep_until,
    next_trading_moment,
    sleep_chunks,
    sleep_until,
    split_groups,
    wait_for_next_trading_day,
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
# Hourly-mode cutoff: before 15:30 on a trading day the streamer runs in
# "hourly" mode (3 always-on indices + yesterday's top-500 traded SZSE stocks,
# one round per hour). At/after 15:30 it switches to "full" mode (all ETF-member
# stocks, rounds until every stock reaches CLOSE_TIME — the original behavior).
HOURLY_MODE_CUTOFF = time(15, 30)

# Afternoon-only start: the streamer waits until this time on trading days
# before entering the main loop. Morning-session data (09:30–11:30) is
# already captured by other streamers; we focus on the afternoon session.
AFTERNOON_START = time(13, 30)

# SZSE indices always streamed every hour during hourly mode. These are the
# flagship SZSE indices that were missing from live data (399001 深证成指,
# 399006 创业板指).
INDEX_ALWAYS_CODES: list = ["399001", "399006"]

# Number of top-traded SZSE stocks (by yesterday's trading_amount) to sample
# each hour during hourly mode.
TOP_TRADED_N = 500

# When a round completes but no stock advanced (e.g. pre-open or lunch break),
# back off this long before the next round instead of hammering the API.
NO_ADVANCE_BACKOFF_SEC = 60.0

# Number of groups the target stock list is split into. Groups are processed
# SEQUENTIALLY (one after another, single thread) — not concurrently.
DEFAULT_GROUPS = 10

# Hard timeout for worker shutdown on termination signal. Workers stuck inside
# asyncio.to_thread (e.g. a DB upsert) can't be cancelled — this prevents the
# gather from hanging forever waiting for them.
SHUTDOWN_TIMEOUT_SEC = 15.0


# ---------------------------------------------------------------------------
# Main streaming loop — rounds, anchoring, clean Ctrl-C
# ---------------------------------------------------------------------------
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
    logger.info("[startup] DB conn (main + index upsert) ready in %.2fs.", _time.time() - t_step)

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

    # --- Load both stock lists + verify ---
    t0 = _time.time()
    logger.info("[startup] calling load_target_stocks(conn) [full ETF-member list]...")
    full_stocks = load_target_stocks(conn)
    logger.info("[startup] Loaded %d full ETF-member SZSE stocks in %.2fs.",
                len(full_stocks), _time.time() - t0)

    t0 = _time.time()
    logger.info("[startup] calling load_yesterday_top_traded_stocks(conn, n=%d)...", TOP_TRADED_N)
    hourly_stocks = load_yesterday_top_traded_stocks(conn, n=TOP_TRADED_N)
    logger.info("[startup] Loaded %d top-traded SZSE stocks (yesterday by amount) in %.2fs.",
                len(hourly_stocks), _time.time() - t0)

    if not full_stocks and not hourly_stocks:
        logger.error("No target stocks found (both full and hourly lists empty); "
                     "ensure sec_composition and stock_basic_stats are populated.")
        for c in (conn, conn_a, conn_b, conn_c, conn_d):
            c.close()
        return
    if not hourly_stocks:
        logger.warning("hourly_stocks empty (no stock_basic_stats data?); "
                       "falling back to full_stocks for hourly mode.")
        hourly_stocks = full_stocks
    if not full_stocks:
        logger.warning("full_stocks empty; using hourly_stocks for full mode too.")
        full_stocks = hourly_stocks

    logger.info(
        "[startup] stream_szse_price started: hourly=%d stocks (top-%d by amt), "
        "full=%d stocks (ETF members); %d always-on indices=%s; "
        "cutoff=%s; cooldown=%.0fs once=%s",
        len(hourly_stocks), TOP_TRADED_N, len(full_stocks),
        len(INDEX_ALWAYS_CODES), INDEX_ALWAYS_CODES,
        HOURLY_MODE_CUTOFF.strftime("%H:%M"), poll_interval, once,
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
    latest_bar_time: dict = {}
    emitted_map: dict = {}
    emitted_map_idx: dict = {}

    try:
        # --- Afternoon-only start: on trading days, wait until AFTERNOON_START
        # (13:30) before entering the main loop. Morning-session data is
        # already captured by other streamers; we focus on afternoon. ---
        _fs_now = datetime.now()
        _fs_today = _fs_now.date()
        if is_trading_day(_fs_today) and _fs_now.time() < AFTERNOON_START:
            _start_dt = datetime.combine(_fs_today, AFTERNOON_START)
            logger.info(
                "[startup] Before afternoon start time (%s); sleeping until %s "
                "before entering main loop.",
                AFTERNOON_START.strftime("%H:%M"),
                _start_dt.strftime("%Y-%m-%d %H:%M"),
            )
            await async_sleep_until(_start_dt)
            logger.info("[startup] Afternoon start time reached; entering main loop.")

        while True:
            now = datetime.now()
            today = now.date()
            trading_today = is_trading_day(today)

            # ---- Anchor: pick / refresh the biz day we are collecting ----
            if current_biz_day is None:
                if not trading_today:
                    # Non-trading day: sleep until next trading moment.
                    # CSV backfill is handled by download/archive modules.
                    nxt = next_trading_moment(now)
                    logger.info(
                        "Non-trading day (%s); sleeping until %s.",
                        today, nxt.strftime("%Y-%m-%d %H:%M"),
                    )
                    await async_sleep_until(nxt)
                    continue
                else:
                    current_biz_day = today
                latest_bar_time = {}
                emitted_map = {}
                emitted_map_idx = {}
                logger.info("Anchored to biz day %s.", current_biz_day)
            elif today > current_biz_day and trading_today:
                logger.info("New biz day %s reached (was %s); re-anchoring.",
                            today, current_biz_day)
                current_biz_day = today
                latest_bar_time = {}
                emitted_map = {}
                emitted_map_idx = {}

            # ---- Determine mode: hourly (before 15:30) or full (after) ----
            if trading_today and now.time() < HOURLY_MODE_CUTOFF:
                mode = "hourly"
                active_stocks = hourly_stocks
            else:
                mode = "full"
                active_stocks = full_stocks

            # Ensure latest_bar_time / emitted_map cover all active stocks.
            for code, _ in active_stocks:
                if code not in latest_bar_time:
                    latest_bar_time[code] = None
                if code not in emitted_map:
                    emitted_map[code] = set()

            # ================================================================
            # HOURLY MODE: 3 indices + top-500 stocks, one round per hour
            # ================================================================
            if mode == "hourly":
                # ---- Guard: before AFTERNOON_START (13:30) on a trading
                # day, the market hasn't opened yet — no data to fetch.
                # Sleep until the session starts instead of wasting 2+
                # hours on pointless API calls returning 0 bars. ---------
                if trading_today and now.time() < AFTERNOON_START:
                    _ast_dt = datetime.combine(today, AFTERNOON_START)
                    logger.info(
                        "Hourly mode but before afternoon start (%s); "
                        "no market data yet — sleeping until %s.",
                        AFTERNOON_START.strftime("%H:%M"),
                        _ast_dt.strftime("%Y-%m-%d %H:%M"),
                    )
                    await async_sleep_until(_ast_dt)
                    continue

                round_start = _time.time()
                round_label = now.strftime("%H:%M:%S")
                logger.info(
                    "=== HOURLY round @ %s biz=%s: %d indices + %d stocks (top-%d by amt) ===",
                    round_label, current_biz_day,
                    len(INDEX_ALWAYS_CODES), len(active_stocks), TOP_TRADED_N,
                )

                # 1. Fetch 3 always-on indices (sequential, antibot sleep).
                n_idx_ident, n_idx_bars = await _run_index_round(
                    INDEX_ALWAYS_CODES, session, host_tracker, emitted_map_idx,
                    current_biz_day, poll_interval, conn,
                )

                # 2. Fetch top-500 stocks via 4 parallel workers.
                unfinished = [(c, nm) for (c, nm) in active_stocks
                              if _not_finished(latest_bar_time.get(c))]
                n_stock_ident = n_stock_bars = 0
                if unfinished:
                    round_groups = split_groups(unfinished, n_groups)
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
                    except (asyncio.CancelledError, KeyboardInterrupt):
                        state["stop"] = True
                        for w in workers:
                            if not w.done():
                                w.cancel()
                        try:
                            await asyncio.wait_for(
                                asyncio.gather(*workers, return_exceptions=True),
                                timeout=SHUTDOWN_TIMEOUT_SEC,
                            )
                        except asyncio.TimeoutError:
                            logger.warning(
                                "Workers did not shut down within %.0fs; forcing exit.",
                                SHUTDOWN_TIMEOUT_SEC,
                            )
                        raise
                    n_stock_ident = len(state["identity"])
                    n_stock_bars = len(state["bars"])
                    if state["bars"]:
                        write_cycle_csv(datetime.now(), state["bars"])

                round_elapsed = _time.time() - round_start
                logger.info(
                    "=== HOURLY round done @ %s: indices=%d bars, stocks=%d bars / %d stocks "
                    "in %.1fs ===",
                    round_label, n_idx_bars, n_stock_bars,
                    len(unfinished) if unfinished else 0, round_elapsed,
                )

                if once:
                    logger.info("--once set; exiting after one hourly round.")
                    break

                # ---- If no bars were produced at all, the market is
                # closed or data hasn't been published yet. Sleep until
                # the next trading session instead of just the next hour.
                total_bars = n_idx_bars + n_stock_bars
                if total_bars == 0:
                    if now.time() >= CLOSE_TIME:
                        # Post-close: jump directly to next trading day's
                        # 13:30 (afternoon-only streamer).
                        next_day = current_biz_day + timedelta(days=1)
                        while not is_trading_day(next_day):
                            next_day += timedelta(days=1)
                        nxt = datetime.combine(next_day, AFTERNOON_START)
                    else:
                        nxt = next_trading_moment(datetime.now())
                    if nxt > datetime.now():
                        logger.info(
                            "No bars produced this round (indices=%d, "
                            "stocks=%d); market closed or data "
                            "unavailable — waiting until %s.",
                            n_idx_bars, n_stock_bars,
                            nxt.strftime("%Y-%m-%d %H:%M"),
                        )
                        await async_sleep_until(nxt)
                        continue

                # If every active stock has reached CLOSE_TIME, no more data
                # will arrive today. Since this streamer is afternoon-only,
                # sleep directly until the next trading day's 13:30 instead
                # of wasting a wake cycle at 09:30.
                still_unfinished = [(c, nm) for (c, nm) in active_stocks
                                    if _not_finished(latest_bar_time.get(c))]
                if not still_unfinished:
                    next_day = current_biz_day + timedelta(days=1)
                    while not is_trading_day(next_day):
                        next_day += timedelta(days=1)
                    nxt = datetime.combine(next_day, AFTERNOON_START)
                    logger.info(
                        "All %d stocks finished for biz day %s; no more data today — "
                        "waiting until next trading session %s.",
                        len(active_stocks), current_biz_day,
                        nxt.strftime("%Y-%m-%d %H:%M"),
                    )
                    await async_sleep_until(nxt)
                    continue

                # Sleep until next HH:00 boundary.
                sleep_sec = _seconds_until_next_hour()
                logger.info("Hourly round done; sleeping %.0fs until next hour boundary.",
                            sleep_sec)
                await async_sleep_chunks(sleep_sec)
                continue

            # ================================================================
            # FULL MODE: all ETF-member stocks, rounds until all reach close
            # (original behavior — runs after 15:30 or on non-trading days
            # after anchoring)
            # ================================================================
            # Compute unfinished stocks (latest time < 15:00).
            unfinished = [(c, nm) for (c, nm) in active_stocks
                          if _not_finished(latest_bar_time.get(c))]
            n_unfinished = len(unfinished)
            n_finished = len(active_stocks) - n_unfinished

            if n_unfinished == 0:
                # All stocks finished for today — sleep until next
                # trading moment. CSV backfill is handled by
                # download/archive modules.
                nxt = next_trading_moment(datetime.now())
                logger.info(
                    "All %d stocks finished for biz day %s; sleeping until %s.",
                    len(active_stocks), current_biz_day,
                    nxt.strftime("%Y-%m-%d %H:%M"),
                )
                if once:
                    break
                await async_sleep_until(nxt)
                continue

            # ---- Run ONE round via 4 parallel async workers ----
            round_groups = split_groups(unfinished, n_groups)
            round_start = _time.time()
            round_label = datetime.now().strftime("%H:%M:%S")
            logger.info(
                "=== FULL round @ %s biz=%s: %d unfinished / %d finished (groups=%d, 4 parallel procs: A/C/D primary + B fallback) ===",
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
            except (asyncio.CancelledError, KeyboardInterrupt):
                state["stop"] = True
                for w in workers:
                    if not w.done():
                        w.cancel()
                try:
                    await asyncio.wait_for(
                        asyncio.gather(*workers, return_exceptions=True),
                        timeout=SHUTDOWN_TIMEOUT_SEC,
                    )
                except asyncio.TimeoutError:
                    logger.warning(
                        "Workers did not shut down within %.0fs; forcing exit.",
                        SHUTDOWN_TIMEOUT_SEC,
                    )
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
                    "=== FULL round done @ %s: %d bars / %d stocks (identity=%d) "
                    "in %.1fs (%.0f bars/s); csv=%s (DB upserted per-stock) ===",
                    round_label, len(all_bars), codes_with_bars, len(all_identity),
                    round_elapsed,
                    len(all_bars) / round_elapsed if round_elapsed > 0 else 0.0,
                    csv_path.name if csv_path else "(none)",
                )
            else:
                logger.info(
                    "=== FULL round done @ %s: no new bars in %.1fs ===",
                    round_label, round_elapsed,
                )

            n_finished_now = sum(1 for c, _ in active_stocks if not _not_finished(latest_bar_time.get(c)))
            logger.info(
                "progress: %d/%d stocks finished (%.1f%%), %d still < %s",
                n_finished_now, len(active_stocks), n_finished_now * 100.0 / len(active_stocks),
                len(active_stocks) - n_finished_now, CLOSE_TIME.strftime("%H:%M"),
            )

            if once:
                logger.info("--once set; exiting after one full round.")
                break

            # ---- Pace the next round ----
            if not advanced:
                sleep_sec = NO_ADVANCE_BACKOFF_SEC
                logger.info("no stock advanced this round; backing off %.0fs.", sleep_sec)
            else:
                sleep_sec = poll_interval - round_elapsed
            if sleep_sec > 0:
                await async_sleep_chunks(sleep_sec)
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
