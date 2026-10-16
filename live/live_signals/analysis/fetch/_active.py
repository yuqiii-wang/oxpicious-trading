"""Active rule-cell fetchers of the live breach check
(live.live_signals.analysis.fetch._active).

The RULE CELLS of analysis_signals.signal_rules ARE the current
threshold set the live tier breaches (the rules-era rewire, 2026-10-15
— signal_thresholds was dropped with the 2026-10-14 redesign): every
row is current (the pipeline wipes and rewrites the family scope each
run), one row per (code, sec_type, signal_type, signal_sub_type,
trend, regime_state, action) cell, carrying its own action, bar
definition (rule_params.bar_def) and confidence scale
(rule_params.confidence) — plus the per-code market-regime label and
the signal rules tier's own TREND classification recorded as breach
context. Plain SELECTs — the direction / decision live on the stored
rows.
"""
from __future__ import annotations

import datetime
import json

from asyncpg import Connection

from _common.labels import (
    ACTION_CODE,
    SEC_TYPE_CODE,
    SEC_TYPE_LABEL,
    SIGNAL_TYPE_CODE,
    SIGNAL_TYPE_LABEL,
    SIGNAL_SUB_TYPE_LABEL,
)

from live.live_signals.analysis.fetch._values import SIGNAL_VALUE_SOURCE
from live.live_signals.config import SIGNALS_TABLE, TREND_DAYS_TABLE


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
    gate; 0 (calm) when the day has no state row — the engine's own
    default (prepare_frame's regime join fills calm)."""
    row = await conn.fetchrow(
        """
        SELECT regime FROM stats.market_regimes r
        WHERE r.sec_type = $1 AND r.code = $2 AND r.date = $3
        LIMIT 1
        """,
        sec_type, code, on_date,
    )
    return row["regime"] if row is not None else 0


async def fetch_trend_state(
    conn: Connection, sec_type: str, code: str,
    on_date: datetime.date,
) -> int | None:
    """The code's TREND code at the latest analysis.trend_days row
    at-or-before ``on_date`` — the signal rules tier's OWN
    classification (trends.attach_trend: 0 top / 1 bottom / 2 rise /
    3 fall / 5 unknown — rise/fall the RSI re-entry episodes since
    2026-10-15; trailing inputs shifted one row so the label is known
    at its own day's close). The DAY-CONTEXT MATCH input: only the
    rule cell whose (trend, regime) equals the day's context may fire.
    None when the store has no row (the analysis_signals pipeline
    never ran / the code is too young) — no cell can then match."""
    row = await conn.fetchrow(
        f"""
        SELECT trend FROM {TREND_DAYS_TABLE}
        WHERE sec_type = $1 AND code = $2 AND date <= $3
        ORDER BY date DESC LIMIT 1
        """,
        SEC_TYPE_CODE[sec_type], code, on_date,
    )
    return None if row is None else row["trend"]


async def fetch_active_signals(
    conn: Connection, sec_type: str, code: str,
) -> list[dict]:
    """The code's ACTIVE RULE CELLS (the current threshold set): every
    analysis_signals.signal_rules row of the LIVE-WIRED families (the
    SIGNAL_VALUE_SOURCE keys — mov_rsi / bollinger; the options and
    ma_cross families are not live-evaluable and never ride this set).
    Each row carries its own action, its (trend, regime) cell key and
    its SELF-ADAPTIVE prepared params — the bar definition
    (rule_params.bar_def: rolling_quantile for mov_rsi's per-code
    percentile tail / fixed_sigma for bollinger's ±k) and the
    confidence scale (rule_params.confidence: base_pct amplified by
    depth_factor over the breach depth, capped). The live tier decides
    buy/sell from THESE, never from family logic. wait rows are
    EXCLUDED — they are the strategy tier's not-trading-yet records
    (options_wall's VRP-declined twin; the sample-STARVED cells since
    2026-10-16), never a live bar."""
    live_type_codes = [
        SIGNAL_TYPE_CODE[t] for t in sorted(SIGNAL_VALUE_SOURCE)
    ]
    rows = await conn.fetch(
        f"SELECT s.code, s.sec_type, s.signal_type, s.signal_sub_type, "
        f"       s.action, s.trend, s.regime_state, "
        f"       s.rule_params->'bar_def'    AS bar_def, "
        f"       s.rule_params->'confidence' AS confidence, "
        f"       s.rule_params->>'window'    AS window "
        f"FROM {SIGNALS_TABLE} s "
        f"WHERE s.sec_type = $1 AND s.code = $2 "
        f"  AND s.signal_type = ANY($3) "
        f"  AND s.action <> {ACTION_CODE['wait']} "
        f"ORDER BY s.signal_type, s.signal_sub_type, s.trend, "
        f"         s.regime_state, s.action",
        # the stored sec_type / signal_type columns are SMALLINT label
        # codes (the label-code contract) — the param carries the code
        # and the returned codes decode to the labels the rest of the
        # live tier speaks (SIGNAL_VALUE_SOURCE / the live row write)
        SEC_TYPE_CODE[sec_type], code, live_type_codes,
    )
    out: list[dict] = []
    for r in rows:
        rec = dict(r)
        rec["sec_type"] = SEC_TYPE_LABEL.get(rec["sec_type"], rec["sec_type"])
        rec["signal_type"] = SIGNAL_TYPE_LABEL.get(
            rec["signal_type"], rec["signal_type"],
        )
        # signal_sub_type is its SMALLINT code — decode to the label
        # here: the live tier's evaluator and row write speak the label
        # strings (live.live_signals keeps TEXT)
        rec["signal_sub_type"] = SIGNAL_SUB_TYPE_LABEL.get(
            rec["signal_sub_type"], rec["signal_sub_type"],
        )
        # asyncpg hands jsonb fragments back as JSON TEXT without a
        # codec — parse the cell's bar_def / confidence to dicts once
        # here (the evaluator indexes them)
        for key in ("bar_def", "confidence"):
            if isinstance(rec.get(key), str):
                rec[key] = json.loads(rec[key])
        if rec["window"] is not None:
            rec["window"] = int(rec["window"])
        out.append(rec)
    return out


async def fetch_active_codes(conn: Connection, sec_type: str) -> list[str]:
    """Codes with rule cells of the LIVE-WIRED families for the
    sec_type (the batch mode's universe) — wait rows excluded, the
    observational records never define the live universe."""
    live_type_codes = [
        SIGNAL_TYPE_CODE[t] for t in sorted(SIGNAL_VALUE_SOURCE)
    ]
    rows = await conn.fetch(
        f"SELECT DISTINCT code FROM {SIGNALS_TABLE} "
        f"WHERE sec_type = $1 AND signal_type = ANY($2) "
        f"  AND action <> {ACTION_CODE['wait']} ORDER BY code",
        SEC_TYPE_CODE[sec_type], live_type_codes,
    )
    return [r["code"] for r in rows]
