"""Active threshold-set fetchers of the live breach check
(live.live_signals.analysis.fetch._active).

The is_active rows of analysis_signals.signal_strategies ARE the
current threshold set the live tier breaches, plus the per-code
market-hype probe recorded as breach context. Plain SELECTs — the
direction / decision live on the stored rows.
"""
from __future__ import annotations

import datetime

from asyncpg import Connection

from live.live_signals.config import SIGNALS_TABLE


async def fetch_is_market_hyped(
    conn: Connection, sec_type: str, code: str,
    on_date: datetime.date,
) -> bool:
    """Whether ``on_date`` sits inside one of the code's
    stats.mov_ave_market_hypes episodes (ANY min_checkin_period — the
    same union convention the forecast bucket splits use; the batch-side
    sibling is analysis_forecasts' fetch_hyped_episodes, this is the
    per-code point-probe shape the live tier needs). Partition-pruned by
    the code-leading PK. As-of-safe by construction: episodes are static
    historical intervals, so a --date D replay sees exactly the D
    verdict. RECORD context for the breach row, never a gate."""
    row = await conn.fetchrow(
        """
        SELECT 1 FROM stats.mov_ave_market_hypes e
        WHERE e.sec_type = $1 AND e.code = $2
          AND e.start_date <= $3 AND e.end_date >= $3
        LIMIT 1
        """,
        sec_type, code, on_date,
    )
    return row is not None


async def fetch_active_signals(
    conn: Connection, sec_type: str, code: str,
) -> list[dict]:
    """The code's ACTIVE signal strategies (current threshold set): the
    is_active rows of analysis_signals.signal_strategies for the
    resolved sec_type — all signal types / sub types. Each row carries
    its own action / signal_threshold / confidence — the live tier
    decides buy/sell from THESE, never from family logic. confidence
    is returned already on the live 0-100 scale: ROUND(confidence *
    100)::int of the source's forecast confidence (the bucket's
    mixed-row reverse_prob; exact NUMERIC rounding — do NOT re-scale
    in Python, float math would round ties differently)."""
    rows = await conn.fetch(
        f"SELECT code, sec_type, signal_type, signal_sub_type, "
        f"       start_date, end_date, side, "
        f"       action, signal_threshold::float8 AS signal_threshold, "
        f"       ROUND(COALESCE(confidence, 0) * 100)::int AS confidence, "
        f"       params "
        f"FROM {SIGNALS_TABLE} "
        f"WHERE sec_type = $1 AND code = $2 AND is_active "
        f"ORDER BY signal_type, signal_sub_type",
        sec_type, code,
    )
    return [dict(r) for r in rows]


async def fetch_active_codes(conn: Connection, sec_type: str) -> list[str]:
    """Codes with ACTIVE signal configs for the sec_type (the batch
    mode's universe: every code whose thresholds are live)."""
    rows = await conn.fetch(
        f"SELECT DISTINCT code FROM {SIGNALS_TABLE} "
        f"WHERE sec_type = $1 AND is_active ORDER BY code",
        sec_type,
    )
    return [r["code"] for r in rows]
