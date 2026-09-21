"""Active threshold-set fetchers of the live breach check
(live.live_signals.analysis.fetch._active).

The is_active rows of analysis_signals.signal_strategies ARE the
current threshold set the live tier breaches, plus the per-code
market-regime label recorded as breach context. Plain SELECTs — the
direction / decision live on the stored rows.
"""
from __future__ import annotations

import datetime

from asyncpg import Connection

from live.live_signals.config import SIGNALS_TABLE


async def fetch_regime_state(
    conn: Connection, sec_type: str, code: str,
    on_date: datetime.date,
) -> str:
    """The code's market regime ON ``on_date`` — the
    stats.market_regimes day label (calm / hot / panic / quiet;
    replaces the retired fetch_is_market_hyped episode probe). A single
    (code, date) point lookup, partition-pruned by the code-leading PK.
    As-of-safe by construction: the daily labels are static historical
    rows built from shift-1 trailing inputs (every label is known at
    its own day's close), so a --date D replay sees exactly the D
    verdict. RECORD context for the breach row, never a gate; 'calm'
    when the day has no state row."""
    row = await conn.fetchrow(
        """
        SELECT regime FROM stats.market_regimes r
        WHERE r.sec_type = $1 AND r.code = $2 AND r.date = $3
        LIMIT 1
        """,
        sec_type, code, on_date,
    )
    return row["regime"] if row is not None else "calm"


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
