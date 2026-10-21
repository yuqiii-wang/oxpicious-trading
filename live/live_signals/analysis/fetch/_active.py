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
rows. The starvation scale (a cell under its sample floor trades at
min(n/floor, 1) of the confidence, 2026-10-19) is RECOMPUTED here
since 2026-10-20 (the gate digest left the slimmed cell snapshot):
the LATEST performance snapshot's n_episodes against the rule's own
rule_params.gate.sample_floor constant — merged into the confidence
dict like the base. The GATED PORTFOLIO CASCADE (the confidence
INCREASE from a passing portfolio group) reads the PORTFOLIO tier:
the strongest is_valid portfolio_signal_rules group claiming the
cell's signal_rule_id, its latest history_portfolio_signal_performance
snapshot's gate.conf_scale.
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
from live.live_signals.config import (
    PORTFOLIO_PERFORMANCE_TABLE,
    PORTFOLIO_RULES_TABLE,
    SIGNALS_TABLE,
    SIGNAL_PERFORMANCE_TABLE,
    TREND_DAYS_TABLE,
)

# The hedge_pairs family's SOURCED component stamps
# (analysis.analysis_signals.engines.signal_families.hedge_pairs
# .engine._ComponentRsiEntry): REAL mov_rsi cells (signal_type 0) at
# the market + leg codes, POOLED on both axes — the portfolio tier's
# SOURCE layer, whose history replay is deliberately dropped ("the
# recorded events are the ORCHESTRATED composites'"). They are
# orchestration inputs, never standalone live bars — and the
# evaluator's pooled day-context match ('all' 4 matches any day state)
# would otherwise wake them at exactly the live-checked codes
# (HP_MARKET_CODE 000300 is a study index). Kept in step with the
# shim's class name.
ORCHESTRATION_ENTRIES = ("_ComponentRsiEntry",)


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
        SEC_TYPE_CODE[sec_type], code, on_date,
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
    as_of: datetime.date | None = None,
) -> list[dict]:
    """The code's ACTIVE RULE CELLS (the current threshold set): every
    analysis_signals.signal_rules row of the LIVE-WIRED families (the
    SIGNAL_VALUE_SOURCE keys — mov_rsi / bollinger; the options and
    ma_cross families are not live-evaluable and never ride this set).
    Each row carries its own action, its (trend, regime) cell key and
    its CONSTANT rule definition — the bar definition
    (rule_params.bar_def: rolling_quantile for mov_rsi's per-code
    percentile tail / fixed_sigma for bollinger's ±k) and the
    confidence SCALE (rule_params.confidence: depth_factor /
    depth_cap). The confidence BASE is the cell's MEASURED dir_ave —
    the latest history_signal_performance snapshot at-or-before
    ``as_of`` (the 2026-10-19 split, slimmed 2026-10-20 to the cell's
    own measure block; None as_of = the latest snapshot), merged
    into the confidence dict here (the STARVATION scale RECOMPUTED
    from the same snapshot's n_episodes + the rule's
    gate.sample_floor constant — the digest left the slimmed snapshot;
    the live evaluator applies it like the history writer does). The live tier decides buy/sell
    from THESE, never from family logic. wait rows are EXCLUDED — they
    are the strategy tier's observational records (options_wall's
    VRP-declined twin; the starvation wait era ended 2026-10-19 —
    under-floor cells now trade at a reduced confidence), never a live
    bar; the hedge_pairs ORCHESTRATION stamps (_ComponentRsiEntry) are
    excluded too — the portfolio source layer never fires standalone."""
    live_type_codes = [
        SIGNAL_TYPE_CODE[t] for t in sorted(SIGNAL_VALUE_SOURCE)
    ]
    rows = await conn.fetch(
        f"SELECT s.code, s.sec_type, s.signal_type, s.signal_sub_type, "
        f"       s.action, s.trend, s.regime_state, "
        f"       s.rule_params->'bar_def'    AS bar_def, "
        f"       s.rule_params->'confidence' AS confidence, "
        f"       s.rule_params->>'window'    AS window, "
        f"       perf.base_pct, perf.sample_scale, pf.portfolio_scale "
        f"FROM {SIGNALS_TABLE} s "
        # the confidence base = the cell's measured dir_ave at the
        # LATEST performance snapshot at-or-before the checked date (a
        # --date replay uses the measurement known that day; the cell
        # key equijoins the snapshot's PK minus its date). The
        # STARVATION scale is RECOMPUTED here (2026-10-20, the gate
        # digest left the cell snapshot with the combined-tier move):
        # min(n_episodes / the rule's gate.sample_floor constant, 1)
        # — NULL when the rule carries no floor (no haircut)
        f"LEFT JOIN LATERAL ( "
        f"    SELECT p.dir_ave_pct AS base_pct, "
        f"           LEAST(p.n_episodes::float8 / NULLIF("
        f"               (s.rule_params->'gate'"
        f"                ->>'sample_floor')::float8, 0), "
        f"               1.0) AS sample_scale "
        f"    FROM {SIGNAL_PERFORMANCE_TABLE} p "
        f"    WHERE p.code = s.code AND p.sec_type = s.sec_type "
        f"      AND p.signal_type = s.signal_type "
        f"      AND p.signal_sub_type = s.signal_sub_type "
        f"      AND p.trend = s.trend "
        f"      AND p.regime_state = s.regime_state "
        f"      AND p.action = s.action "
        f"      AND ($4::date IS NULL OR p.date <= $4::date) "
        f"    ORDER BY p.date DESC LIMIT 1 "
        f") perf ON TRUE "
        # the GATED PORTFOLIO CASCADE (2026-10-20, re-homed by the
        # cell-level redesign): the strongest PASSING portfolio group
        # claiming this cell's signal_rule_id (an is_in_portfolio
        # member), that group's LATEST audit snapshot's conf_scale —
        # the confidence INCREASE from the portfolio tier; NULL = no
        # passing group claimed the cell (the default — never a
        # discount)
        f"LEFT JOIN LATERAL ( "
        f"    SELECT (q.gate->>'conf_scale')::float8 AS portfolio_scale "
        f"    FROM {PORTFOLIO_RULES_TABLE} g "
        f"    JOIN LATERAL ( "
        f"        SELECT q.gate "
        f"        FROM {PORTFOLIO_PERFORMANCE_TABLE} q "
        f"        WHERE q.code = g.code "
        f"          AND q.portfolio_type = g.portfolio_type "
        f"          AND ($4::date IS NULL OR q.date <= $4::date) "
        f"        ORDER BY q.date DESC LIMIT 1 "
        f"    ) q ON TRUE "
        f"    WHERE s.signal_rule_id = ANY(g.signal_rule_ids) "
        f"      AND g.is_valid "
        f"    ORDER BY (q.gate->>'conf_scale')::float8 DESC NULLS LAST "
        f"    LIMIT 1 "
        f") pf ON TRUE "
        f"WHERE s.sec_type = $1 AND s.code = $2 "
        f"  AND s.signal_type = ANY($3) "
        f"  AND s.action <> {ACTION_CODE['wait']} "
        f"  AND s.rule_entry <> ALL($5::text[]) "
        f"ORDER BY s.signal_type, s.signal_sub_type, s.trend, "
        f"         s.regime_state, s.action",
        # the stored sec_type / signal_type columns are SMALLINT label
        # codes (the label-code contract) — the param carries the code
        # and the returned codes decode to the labels the rest of the
        # live tier speaks (SIGNAL_VALUE_SOURCE / the live row write)
        SEC_TYPE_CODE[sec_type], code, live_type_codes, as_of,
        list(ORCHESTRATION_ENTRIES),
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
        # the confidence base (the measured dir_ave, PERCENT) joins the
        # rule's scale here — rules_confidence_bp reads one confidence
        # dict; a cell without a snapshot (pre-migration residue) keeps
        # no base and the breach confidence falls to the 0 default
        base_pct = rec.pop("base_pct", None)
        if base_pct is not None:
            conf = rec["confidence"] \
                if isinstance(rec["confidence"], dict) else {}
            rec["confidence"] = {**conf, "base_pct": base_pct}
        # the starvation haircut (min(n/floor, 1), 2026-10-19;
        # recomputed in the fetch since the 2026-10-20 slimming) — the
        # evaluator multiplies it into the breach confidence exactly
        # like the history writer's confidence_pct; None (no floor on
        # the rule / no snapshot) means no haircut
        sample_scale = rec.pop("sample_scale", None)
        if sample_scale is not None:
            conf = rec["confidence"] \
                if isinstance(rec["confidence"], dict) else {}
            rec["confidence"] = {**conf,
                                 "sample_scale": float(sample_scale)}
        # the GATED PORTFOLIO CASCADE (2026-10-20, re-homed by the
        # cell-level redesign): the confidence INCREASE from the
        # strongest passing portfolio group claiming the cell's
        # signal_rule_id (the portfolio tables lateral); None = no
        # passing group claimed the cell (the default 1.0 — never a
        # discount)
        portfolio_scale = rec.pop("portfolio_scale", None)
        if portfolio_scale is not None:
            conf = rec["confidence"] \
                if isinstance(rec["confidence"], dict) else {}
            rec["confidence"] = {**conf,
                                 "portfolio_scale": float(portfolio_scale)}
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
