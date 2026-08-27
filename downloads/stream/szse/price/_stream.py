"""SZSE price streaming main loop — biz-day anchoring, mode switching, pacing.

Split out of ``__main__.py``; the per-mode round logic lives in
``_cycles.py``. Behavior is unchanged from the original inline
implementation in ``stream()``:

  * HOURLY MODE (before 15:30 on trading days): 2 always-on SZSE indices plus
    yesterday's top-500 traded SZSE stocks, one round per hour.
  * FULL MODE (at/after 15:30): the full ETF-member stock list, rounds until
    every stock reaches CLOSE_TIME (15:00).

Afternoon-only start (13:30); no morning-session fetching. See ``__main__.py``
for the full architecture description (worker layout, aggregation, anti-bot).
"""
from __future__ import annotations

import asyncio
import time as _time
from datetime import datetime
from typing import List, Tuple

from downloads._common import is_trading_day, setup_logger

from ._constants import (
    AFTERNOON_START,
    DEFAULT_GROUPS,
    DEFAULT_POLL_INTERVAL_SEC,
    HOURLY_MODE_CUTOFF,
)
from ._cycles import full_cycle, hourly_cycle
from ._startup import (
    StreamConns,
    build_http_stack,
    describe_startup,
    import_heavy_modules,
    load_stock_lists,
    open_db_connections,
)
from ._workers import (
    async_sleep_until,
    next_trading_moment,
)

logger = setup_logger("stream_szse")

StockList = List[Tuple[str, str]]


async def stream(
    poll_interval: float = DEFAULT_POLL_INTERVAL_SEC,
    n_groups: int = DEFAULT_GROUPS,
    once: bool = False,
) -> None:
    """Run the SZSE price streamer until cancelled (Ctrl-C) or --once."""
    t_stream = _time.time()

    # --- Startup: DB connections, target lists, heavy modules, HTTP stack ---
    conns: StreamConns = open_db_connections()

    logger.info("[startup] loading full ETF-member + top-traded stock lists...")
    full_stocks: StockList
    hourly_stocks: StockList
    full_stocks, hourly_stocks = load_stock_lists(conns.conn)

    if not full_stocks and not hourly_stocks:
        logger.error("No target stocks found (both full and hourly lists empty); "
                     "ensure sec_composition and stock_basic_stats are populated.")
        conns.close()
        return

    describe_startup(len(hourly_stocks), len(full_stocks), poll_interval, once)
    import_heavy_modules()
    session, host_tracker = build_http_stack()
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
            # HOURLY MODE: 2 indices + top-500 stocks, one round per hour
            # ================================================================
            if mode == "hourly":
                should_continue = await hourly_cycle(
                    now, today, trading_today, current_biz_day,
                    active_stocks, latest_bar_time, emitted_map, emitted_map_idx,
                    session, host_tracker, conns, n_groups, poll_interval, once,
                )
                if should_continue == "continue":
                    continue
                break

            # ================================================================
            # FULL MODE: all ETF-member stocks, rounds until all reach close
            # (original behavior — runs after 15:30 or on non-trading days
            # after anchoring)
            # ================================================================
            should_break = await full_cycle(
                active_stocks, latest_bar_time, emitted_map, current_biz_day,
                session, host_tracker, conns, n_groups, poll_interval, once,
            )
            if should_break:
                break
    except (asyncio.CancelledError, KeyboardInterrupt):
        logger.info("Termination signal received; cancelling async workers and exiting.")
    finally:
        conns.close()
        try:
            session.close()
        except Exception:
            pass
