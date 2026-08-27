"""SZSE streamer mode cycles — one iteration each of hourly and full modes.

Split out of ``_stream.py``. Each function performs a single round (fetch +
archive + pacing sleeps) and reports a control-flow decision to the main
loop so sleep-until logic stays with its mode.
"""
from __future__ import annotations

import time as _time
from datetime import datetime
from typing import List, Tuple

from downloads._common import setup_logger

from ._constants import (
    AFTERNOON_START,
    INDEX_ALWAYS_CODES,
    NO_ADVANCE_BACKOFF_SEC,
    TOP_TRADED_N,
)
from ._rounds import archive_round_bars, next_afternoon_start, run_stock_round
from ._startup import StreamConns
from ._workers import (
    CLOSE_TIME,
    _not_finished,
    _run_index_round,
    _seconds_until_next_hour,
    async_sleep_chunks,
    async_sleep_until,
    next_trading_moment,
)

logger = setup_logger("stream_szse")

StockList = List[Tuple[str, str]]


async def hourly_cycle(
    now: datetime,
    today,
    trading_today: bool,
    current_biz_day,
    active_stocks: StockList,
    latest_bar_time: dict,
    emitted_map: dict,
    emitted_map_idx: dict,
    session,
    host_tracker,
    conns: StreamConns,
    n_groups: int,
    poll_interval: float,
    once: bool,
) -> str:
    """One iteration of hourly mode. Returns "continue" or "break"."""
    # ---- Guard: before AFTERNOON_START (13:30) on a trading day, the market
    # hasn't opened yet — no data to fetch. Sleep until the session starts
    # instead of wasting 2+ hours on pointless API calls returning 0 bars. ---
    if trading_today and now.time() < AFTERNOON_START:
        _ast_dt = datetime.combine(today, AFTERNOON_START)
        logger.info(
            "Hourly mode but before afternoon start (%s); "
            "no market data yet — sleeping until %s.",
            AFTERNOON_START.strftime("%H:%M"),
            _ast_dt.strftime("%Y-%m-%d %H:%M"),
        )
        await async_sleep_until(_ast_dt)
        return "continue"

    round_start = _time.time()
    round_label = now.strftime("%H:%M:%S")
    logger.info(
        "=== HOURLY round @ %s biz=%s: %d indices + %d stocks (top-%d by amt) ===",
        round_label, current_biz_day,
        len(INDEX_ALWAYS_CODES), len(active_stocks), TOP_TRADED_N,
    )

    # 1. Fetch always-on indices (sequential, antibot sleep).
    n_idx_ident, n_idx_bars = await _run_index_round(
        INDEX_ALWAYS_CODES, session, host_tracker, emitted_map_idx,
        current_biz_day, poll_interval, conns.conn,
    )

    # 2. Fetch top-N stocks via 4 parallel workers.
    unfinished = [(c, nm) for (c, nm) in active_stocks
                  if _not_finished(latest_bar_time.get(c))]
    n_stock_ident = n_stock_bars = 0
    if unfinished:
        state = await run_stock_round(
            unfinished, n_groups, session, host_tracker, emitted_map,
            current_biz_day, latest_bar_time, conns, poll_interval,
        )
        n_stock_ident = len(state["identity"])
        n_stock_bars = len(state["bars"])
        archive_round_bars(state["bars"])

    round_elapsed = _time.time() - round_start
    logger.info(
        "=== HOURLY round done @ %s: indices=%d bars, stocks=%d bars / %d stocks "
        "in %.1fs ===",
        round_label, n_idx_bars, n_stock_bars,
        len(unfinished) if unfinished else 0, round_elapsed,
    )

    if once:
        logger.info("--once set; exiting after one hourly round.")
        return "break"

    # ---- If no bars were produced at all, the market is closed or data
    # hasn't been published yet. Sleep until the next trading session instead
    # of just the next hour. ---
    total_bars = n_idx_bars + n_stock_bars
    if total_bars == 0:
        if now.time() >= CLOSE_TIME:
            # Post-close: jump directly to next trading day's 13:30
            # (afternoon-only streamer).
            nxt = next_afternoon_start(current_biz_day)
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
            return "continue"

    # If every active stock has reached CLOSE_TIME, no more data will arrive
    # today. Since this streamer is afternoon-only, sleep directly until the
    # next trading day's 13:30 instead of wasting a wake cycle at 09:30.
    still_unfinished = [(c, nm) for (c, nm) in active_stocks
                        if _not_finished(latest_bar_time.get(c))]
    if not still_unfinished:
        nxt = next_afternoon_start(current_biz_day)
        logger.info(
            "All %d stocks finished for biz day %s; no more data today — "
            "waiting until next trading session %s.",
            len(active_stocks), current_biz_day,
            nxt.strftime("%Y-%m-%d %H:%M"),
        )
        await async_sleep_until(nxt)
        return "continue"

    # Sleep until next HH:00 boundary.
    sleep_sec = _seconds_until_next_hour()
    logger.info("Hourly round done; sleeping %.0fs until next hour boundary.",
                sleep_sec)
    await async_sleep_chunks(sleep_sec)
    return "continue"


async def full_cycle(
    active_stocks: StockList,
    latest_bar_time: dict,
    emitted_map: dict,
    current_biz_day,
    session,
    host_tracker,
    conns: StreamConns,
    n_groups: int,
    poll_interval: float,
    once: bool,
) -> bool:
    """One round of full mode. Returns True when the streamer should exit."""
    # Compute unfinished stocks (latest time < 15:00).
    unfinished = [(c, nm) for (c, nm) in active_stocks
                  if _not_finished(latest_bar_time.get(c))]
    n_unfinished = len(unfinished)
    n_finished = len(active_stocks) - n_unfinished

    if n_unfinished == 0:
        # All stocks finished for today — sleep until next trading moment.
        # CSV backfill is handled by download/archive modules.
        nxt = next_trading_moment(datetime.now())
        logger.info(
            "All %d stocks finished for biz day %s; sleeping until %s.",
            len(active_stocks), current_biz_day,
            nxt.strftime("%Y-%m-%d %H:%M"),
        )
        if once:
            return True
        await async_sleep_until(nxt)
        return False

    # ---- Run ONE round via 4 parallel async workers ----
    # split_groups filters empty groups, so the effective group count is
    # min(n_groups, len(unfinished)) — logged here before dispatching.
    n_groups_eff = min(n_groups, max(1, len(unfinished)))
    round_start = _time.time()
    round_label = datetime.now().strftime("%H:%M:%S")
    logger.info(
        "=== FULL round @ %s biz=%s: %d unfinished / %d finished (groups=%d, 4 parallel procs: A/C/D primary + B fallback) ===",
        round_label, current_biz_day, n_unfinished, n_finished, n_groups_eff,
    )

    prev_latest = dict(latest_bar_time)

    state = await run_stock_round(
        unfinished, n_groups, session, host_tracker, emitted_map,
        current_biz_day, latest_bar_time, conns, poll_interval,
    )

    round_elapsed = _time.time() - round_start
    all_identity = state["identity"]
    all_bars = state["bars"]

    advanced = any(latest_bar_time.get(c) != prev_latest.get(c) for c, _ in unfinished)

    # Bars were already upserted per-stock during the round; here we only
    # archive the full cycle to CSV and log the summary.
    if all_bars:
        csv_path = archive_round_bars(all_bars)
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
        return True

    # ---- Pace the next round ----
    if not advanced:
        sleep_sec = NO_ADVANCE_BACKOFF_SEC
        logger.info("no stock advanced this round; backing off %.0fs.", sleep_sec)
    else:
        sleep_sec = poll_interval - round_elapsed
    if sleep_sec > 0:
        await async_sleep_chunks(sleep_sec)
    return False
