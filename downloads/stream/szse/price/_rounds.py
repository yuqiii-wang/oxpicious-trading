"""SZSE streamer rounds — one stock round via the 4 parallel workers.

Extracted from ``__main__.py``: both hourly and full modes dispatch the same
worker set (A=akshare, C=em_push2his, D=em_pull primary queue; B=szse fallback
queue), so the queue/state/task construction and the shutdown protocol are
shared here. The highest-ranked stocks (list head) are seeded onto B's queue
so the official SZSE source fetches them with priority (split_rank_priority);
cancellation (Ctrl-C) propagates up to the caller after reaping the worker
tasks.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from downloads._common import HostStatusTracker, is_trading_day, setup_logger

from ._constants import (
    AFTERNOON_START,
    B_PRIORITY_TOP_N,
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


def split_rank_priority(
    stocks: List[Tuple[str, str]],
    b_top_n: int,
    n_groups: int,
) -> Tuple[List[List[Tuple[str, str]]], List[List[Tuple[str, str]]]]:
    """Partition ``stocks`` into (B priority groups, primary groups).

    The list head is the highest rank (hourly mode orders by yesterday's
    trading_amount DESC), so the first ``b_top_n`` stocks become B's
    exclusive slice, kept as ONE group to preserve strict rank order. The
    remainder is round-robin split into ``n_groups`` groups for A/C/D.
    ``b_top_n <= 0`` disables the split (everything goes to the primary
    queue — the original behavior).
    """
    if b_top_n > 0:
        b_groups = split_groups(stocks[:b_top_n], 1)
        rest = stocks[b_top_n:]
    else:
        b_groups, rest = [], stocks
    return b_groups, split_groups(rest, n_groups)


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
    b_top_n: int = B_PRIORITY_TOP_N,
) -> RoundState:
    """Run ONE round of the 4 parallel source workers over ``unfinished``.

    Rank-priority: the highest-ranked ``b_top_n`` unfinished stocks (list
    head) are seeded onto q_fallback so B (szse) fetches them first and
    exclusively — A/C/D never see them; A/C/D circuit-breaker remainders
    handed to q_fallback later queue behind that slice (FIFO). The rest is
    split onto q_primary; A/C/D pull groups and hand failures back through
    q_fallback for B to resume, and B helps on the primary queue once the
    priority slice is done. Bars are upserted per-stock inside the workers;
    returns the round state dict with accumulated "identity" / "bars" lists
    for CSV archiving + summaries.

    On Ctrl-C/cancellation: sets state["stop"], cancels all tasks, waits up to
    SHUTDOWN_TIMEOUT_SEC for them to finish, then re-raises.
    """
    b_priority_groups, round_groups = split_rank_priority(unfinished, b_top_n, n_groups)
    logger.info(
        "rank-priority: top %d codes -> B (szse) exclusive slice; %d stocks -> %d primary groups (A/C/D)",
        sum(len(g) for g in b_priority_groups), len(unfinished) - sum(len(g) for g in b_priority_groups),
        len(round_groups),
    )
    q_primary: asyncio.Queue = asyncio.Queue()
    q_fallback: asyncio.Queue = asyncio.Queue()
    for g in b_priority_groups:
        q_fallback.put_nowait(g)
    for g in round_groups:
        q_primary.put_nowait(g)
    state: RoundState = {
        "identity": [],
        "bars": [],
        "latest_bar_time": latest_bar_time,
        "n_outstanding": len(round_groups) + len(b_priority_groups),
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
