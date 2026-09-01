"""SZSE streamer rounds — one stock round via the 4 parallel workers.

Extracted from ``__main__.py``: both hourly and full modes dispatch the same
worker set (A=akshare, C=em_push2his, D=em_pull primary queue; B=szse fallback
queue), so the queue/state/task construction and the shutdown protocol are
shared here. Cancellation (Ctrl-C) propagates up to the caller after reaping
the worker tasks.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from downloads._common import HostStatusTracker, is_trading_day, setup_logger

from ._constants import (
    AFTERNOON_START,
    COOLDOWN_A_SEC,
    COOLDOWN_C_SEC,
    COOLDOWN_D_SEC,
    SHUTDOWN_TIMEOUT_SEC,
)
from ._io import write_cycle_csv
from ._startup import StreamConns
from ._workers import (
    _parallel_source_worker,
    _szse_worker,
    split_groups,
)

logger = setup_logger("stream_szse")

# State dict shape shared with the workers in _workers.py.
RoundState = Dict[str, object]


def next_afternoon_start(biz_day) -> datetime:
    """First datetime strictly after ``biz_day`` at AFTERNOON_START (16:00).

    Used by hourly mode when all stocks have reached close or no bars were
    produced — this streamer is afternoon-only, so it sleeps directly to the
    next trading day's 16:00 instead of waking at 09:30.
    """
    next_day = biz_day + timedelta(days=1)
    while not is_trading_day(next_day):
        next_day += timedelta(days=1)
    return datetime.combine(next_day, AFTERNOON_START)


async def run_stock_round(
    unfinished: List[Tuple[str, str]],
    n_groups: int,
    session,
    host_tracker: HostStatusTracker,
    emitted_map: Dict[str, set],
    trade_date,
    latest_bar_time: Dict[str, Optional[object]],
    conns: StreamConns,
    poll_interval_b: float,
) -> RoundState:
    """Run ONE round of the 4 parallel source workers over ``unfinished``.

    Splits the stocks into groups on the primary queue; A/C/D pull groups and
    hand failures back through q_fallback for B (szse) to resume. Bars are
    upserted per-stock inside the workers; returns the round state dict with
    accumulated "identity" / "bars" lists for CSV archiving + summaries.

    On Ctrl-C/cancellation: sets state["stop"], cancels all tasks, waits up to
    SHUTDOWN_TIMEOUT_SEC for them to finish, then re-raises.
    """
    round_groups = split_groups(unfinished, n_groups)
    q_primary: asyncio.Queue = asyncio.Queue()
    q_fallback: asyncio.Queue = asyncio.Queue()
    for g in round_groups:
        q_primary.put_nowait(g)
    state: RoundState = {
        "identity": [],
        "bars": [],
        "latest_bar_time": latest_bar_time,
        "n_outstanding": len(round_groups),
        "stop": False,
    }

    workers = [
        asyncio.create_task(_parallel_source_worker(
            "akshare", "A", q_primary, q_fallback, state, session,
            host_tracker, emitted_map, trade_date, COOLDOWN_A_SEC, conns.conn_a)),
        asyncio.create_task(_parallel_source_worker(
            "em_push2his", "C", q_primary, q_fallback, state, session,
            host_tracker, emitted_map, trade_date, COOLDOWN_C_SEC, conns.conn_c)),
        asyncio.create_task(_parallel_source_worker(
            "em_push2", "D", q_primary, q_fallback, state, session,
            host_tracker, emitted_map, trade_date, COOLDOWN_D_SEC, conns.conn_d)),
        asyncio.create_task(_szse_worker(
            q_primary, q_fallback, state, session, host_tracker,
            emitted_map, trade_date, poll_interval_b, conns.conn_b)),
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
    return state


def archive_round_bars(bars: List[dict]):
    """Archive a finished round's bars to CSV (DB already upserted per-stock).

    Returns the written CSV Path (or None).
    """
    if bars:
        return write_cycle_csv(datetime.now(), bars)
    return None
