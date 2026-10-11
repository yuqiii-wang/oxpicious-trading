"""Active threshold-set fetchers of the live breach check
(live.live_signals.analysis.fetch._active).

The is_active rows of analysis_signals.signal_thresholds ARE the
current threshold set the live tier breaches, plus the per-code
market-regime label recorded as breach context. Plain SELECTs — the
direction / decision live on the stored rows.
"""
from __future__ import annotations

import datetime

from asyncpg import Connection

from _common.labels import SEC_TYPE_CODE, SEC_TYPE_LABEL, SIGNAL_TYPE_LABEL

from live.live_signals.config import SIGNALS_TABLE


async def fetch_regime_state(
    conn: Connection, sec_type: str, code: str,
    on_date: datetime.date,
) -> int:
    """The code's market regime CODE ON ``on_date`` — the
    stats.market_regimes day label code (0 calm / 1 hot / 2 panic /
    3 quiet; replaces the retired fetch_is_market_hyped episode probe).
    A single (code, date) point lookup, partition-pruned by the
    code-leading PK. As-of-safe by construction: the daily labels are
    static historical rows built from shift-1 trailing inputs (every
    label is known at its own day's close), so a --date D replay sees
    exactly the D verdict. RECORD context for the breach row, never a
    gate; 0 (calm) when the day has no state row."""
    row = await conn.fetchrow(
        """
        SELECT regime FROM stats.market_regimes r
        WHERE r.sec_type = $1 AND r.code = $2 AND r.date = $3
        LIMIT 1
        """,
        sec_type, code, on_date,
    )
    return row["regime"] if row is not None else 0


async def fetch_active_signals(
    conn: Connection, sec_type: str, code: str,
) -> list[dict]:
    """The code's ACTIVE signal thresholds (current threshold set): the
    is_active rows of analysis_signals.signal_thresholds for the
    resolved sec_type — all signal types / sub types. Each row carries
    its own action / signal_threshold / confidence-scale model — the
    live tier decides buy/sell from THESE, never from family logic,
    and computes the SCALED breach confidence over the breach depth at
    record time (config.breach.scaled_confidence_bp — confidence_base
    + scale_method/scale_base/scale_factor, read as float8; the
    rounding to basis points happens in Python over the actual breach
    excess, which SQL cannot know before the current value resolves).

    (The boost-cohort add-on confidence probe was RETIRED with the
    forecast layer 2026-10-05 — the plain rsi-pct population is the
    only cohort; the confidence_boost column stays in the schema,
    unwritten.)"""
    rows = await conn.fetch(
        f"SELECT s.code, s.sec_type, s.signal_type, s.signal_sub_type, "
        f"       s.start_date, s.end_date, s.side, "
        f"       s.action, s.signal_threshold::float8 AS signal_threshold, "
        f"       s.confidence_base::float8 AS confidence_base, "
        f"       s.scale_method, "
        f"       s.scale_base::float8 AS scale_base, "
        f"       s.scale_factor::float8 AS scale_factor "
        f"FROM {SIGNALS_TABLE} s "
        f"WHERE s.sec_type = $1 AND s.code = $2 AND s.is_active "
        f"ORDER BY s.signal_type, s.signal_sub_type",
        # the stored sec_type / signal_type columns are SMALLINT label
        # codes (the label-code contract) — the param carries the code
        # and the returned codes decode to the labels the rest of the
        # live tier speaks (SIGNAL_VALUE_SOURCE / the live row write)
        SEC_TYPE_CODE[sec_type], code,
    )
    out: list[dict] = []
    for r in rows:
        rec = dict(r)
        rec["sec_type"] = SEC_TYPE_LABEL.get(rec["sec_type"], rec["sec_type"])
        rec["signal_type"] = SIGNAL_TYPE_LABEL.get(
            rec["signal_type"], rec["signal_type"],
        )
        out.append(rec)
    return out


async def fetch_active_codes(conn: Connection, sec_type: str) -> list[str]:
    """Codes with ACTIVE signal configs for the sec_type (the batch
    mode's universe: every code whose thresholds are live)."""
    rows = await conn.fetch(
        f"SELECT DISTINCT code FROM {SIGNALS_TABLE} "
        f"WHERE sec_type = $1 AND is_active ORDER BY code",
        SEC_TYPE_CODE[sec_type],
    )
    return [r["code"] for r in rows]
