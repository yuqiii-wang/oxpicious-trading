"""The signal-amplification orchestration (analysis.analysis_signals.
signal_amplification.run).

run_amplification — the tier's one entry point (the analysis_signals
daily pipeline calls it after the families; the standalone
__main__ wraps it):

  1. build analysis.ohlcv_ext (OhlcvExtBuilder — incremental per-code
     horizon; returns the whole universe's anatomy frame) + attach the
     margin anatomy (MarginAnatomy — the 'margin' parent type's
     trailing rz_balance quantile bars) + the news anatomy
     (NewsAnatomy — the 'news' parent type's market state panel +
     weekly aggregates + drop leg)
  2. detect the amplifier days (AmplifierEngine — the two-level
     amplifier_type + sub_type axis)
  3. load the bound families' fired days (history_signals standalone
     tier) + measure the per-binding evidence
     (AmplificationEvidence)
  4. write BOTH tables:
       signal_amplification_rules   wiped + reinserted per run (the
                                    current-state config, the
                                    signal_rules convention)
       signal_amplification_history force: purge the sec_type scope +
                                    key-chunked COPY; incremental:
                                    copy_or_upsert_split past the
                                    table's max date (idempotent)

Returns (n_rules, n_history) rows written.
"""
from __future__ import annotations

import datetime as dt
import json
import logging

import numpy as np
import pandas as pd

from _common.db_commons import (
    batched_copy_by_key_async,
    chunked_purge_async,
    copy_insert_async,
    copy_or_upsert_split_async,
)
from _common.labels import SEC_TYPE_CODE
from _common.df_utils import host_array

from analysis.analysis_signals import config as cfg
from analysis.analysis_signals.signal_amplification.amplifiers import (
    AMPLIFIER_BAR_DEFS,
    AMPLIFIER_DEFS,
    AMPLIFY_DAY_COLUMNS,
    AmplifierEngine,
)
from analysis.analysis_signals.signal_amplification.margin_anatomy import (
    MarginAnatomy,
)
from analysis.analysis_signals.signal_amplification.news_anatomy import (
    NewsAnatomy,
)
from analysis.analysis_signals.signal_amplification.measurement import (
    AmplificationEvidence,
)
from analysis.analysis_signals.signal_amplification.ohlcv_ext import (
    OhlcvExtBuilder,
)

logger = logging.getLogger(__name__)

__all__ = ["RULES_TABLE", "HISTORY_TABLE", "run_amplification"]

RULES_TABLE = "analysis_signals.signal_amplification_rules"
HISTORY_TABLE = "analysis_signals.signal_amplification_history"
RULE_ENTRY = "AmplifierEngine"

# the anatomy columns the history rows' amplifier_params carry as the
# day's extra facts (beyond the common fact columns; the margin columns
# are NULL for codes without margin data — the MarginAnatomy attach;
# the news state columns NULL outside the panel's coverage — the
# NewsAnatomy attach)
_MARGIN_FACT_COLUMNS = ["margin_balance", "margin_bar_low",
                        "margin_bar_high"]
_NEWS_FACT_COLUMNS = ["sent_z", "lex_tone_z", "flow_af", "flow_neg",
                      "disp_z", "wz", "wfn", "wt"]
_PARAM_FACT_COLUMNS = ["gap_oc", "ret_1d", "close_vs_prev_high",
                       "close_vs_prev_low", "amt_ratio_252",
                       *_MARGIN_FACT_COLUMNS, *_NEWS_FACT_COLUMNS]

# the bound families' signal_type codes (the config matrix's targets)
_BOUND_SIGNAL_TYPES = sorted(
    {b.signal_type for d in AMPLIFIER_DEFS for b in d.bindings})


async def _is_current(conn, sec_type_code: int, horizon) -> bool:
    """The incremental skip (the family_is_current shape): the rules
    scope is non-empty AND the history already reaches the source
    horizon."""
    rules_n = await conn.fetchval(
        f"SELECT count(*) FROM {RULES_TABLE} WHERE sec_type = $1",
        sec_type_code,
    )
    if not rules_n:
        return False
    max_date = await conn.fetchval(
        f"SELECT max(date) FROM {HISTORY_TABLE} WHERE sec_type = $1",
        sec_type_code,
    )
    return max_date is not None and max_date >= horizon


def _amplify_bp(base_bp: int, depth: float | None) -> int:
    """The depth-scaled strength (the CellGate convention), capped at
    |AMP_CAP_BP|; a missing depth scales nothing."""
    d = 0.0 if depth is None or np.isnan(depth) else float(depth)
    scaled = base_bp * (1.0 + cfg.AMP_DEPTH_FACTOR
                        * min(d, cfg.AMP_DEPTH_CAP))
    cap = cfg.AMP_CAP_BP
    return int(round(max(-cap, min(cap, scaled))))


def _materialize(amp_days: pd.DataFrame,
                 evidence: dict[tuple[str, int, int, int], dict],
                 fired_map: dict[tuple[str, int, int, dt.date], int],
                 fired_pairs: set[tuple[str, int]],
                 sec_type_code: int,
                 ) -> tuple[list[dict], list[dict]]:
    """The write boundary: the rules rows (one per (code, signal_type)
    the code's history actually fires — the binding config + evidence)
    and the history rows (one per amplifier day — the day facts + the
    per-binding results, the two-level amplifier_type + sub_type axis).
    The host loop over the small day frame is the sanctioned boundary
    (JSONB payloads are per-row)."""
    by_key_amp = {(d.amplifier_type, d.sub_type): d for d in AMPLIFIER_DEFS}

    # ---- history rows --------------------------------------------------
    history_rows: list[dict] = []
    amp_np = {
        c: host_array(amp_days[c].to_numpy(dtype="object"))
        for c in ("code",)
    }
    date_arr = host_array(amp_days["date"])
    py_dates = date_arr.astype("datetime64[D]").tolist()
    fact_np = {
        c: host_array(amp_days[c].to_numpy(dtype="float64",
                                           na_value=np.nan))
        for c in ("amplifier_type", "sub_type", "gap_pct", "wick_pct",
                  "amt_ratio", "close_pos", "margin_vs_bar", "depth",
                  *_PARAM_FACT_COLUMNS)
    }
    for i in range(len(amp_days)):
        code = str(amp_np["code"][i])
        atype = int(fact_np["amplifier_type"][i])
        sub = int(fact_np["sub_type"][i])
        depth = float(fact_np["depth"][i])
        if np.isnan(depth):
            depth = None
        d = by_key_amp[(atype, sub)]
        day = py_dates[i]
        bindings_payload = []
        for b in d.bindings:
            ev = evidence.get((code, b.signal_type, atype, sub), {})
            # fired = a bound-family history_signals event (the
            # binding's own code/signal_type/action) exists ON THIS DAY
            fired_n = fired_map.get(
                (code, b.signal_type, b.action, day), 0)
            bindings_payload.append({
                "signal_type": b.signal_type,
                "signal_label": b.signal_label,
                "action": b.action,
                "action_label": b.action_label,
                "base_bp": b.base_bp,
                "amplify_bp": _amplify_bp(b.base_bp, depth),
                "fired": fired_n > 0,
                "fired_n": fired_n,
            })

        def _num(col: str) -> float | None:
            v = float(fact_np[col][i])
            return None if np.isnan(v) else round(v, 6)

        params = {
            "bar_def": AMPLIFIER_BAR_DEFS[(atype, sub)],
            "facts": {c: _num(c) for c in _PARAM_FACT_COLUMNS},
        }
        history_rows.append({
            "code": code,
            "sec_type": sec_type_code,
            "amplifier_type": atype,
            "sub_type": sub,
            "date": day,
            "gap_pct": _num("gap_pct"),
            "wick_pct": _num("wick_pct"),
            "amt_ratio": _num("amt_ratio"),
            "close_pos": _num("close_pos"),
            "margin_vs_bar": _num("margin_vs_bar"),
            "depth": depth,
            "bindings": json.dumps(bindings_payload),
            "amplifier_params": json.dumps(params),
        })

    # ---- rules rows ----------------------------------------------------
    # one per (code, signal_type) the code's own history actually fires
    # (any fired day — the config defines the FUTURE amplification; a
    # pair whose family never landed on an amplifier day yet ships an
    # empty evidence block, is_valid FALSE — observational, not disproven)
    bound_pairs: dict[tuple[str, int], list] = {}
    for code, sig in fired_pairs:
        amps = [
            (d, b, evidence.get(
                (code, sig, d.amplifier_type, d.sub_type), {}))
            for d in AMPLIFIER_DEFS for b in d.bindings
            if b.signal_type == sig
        ]
        if amps:
            bound_pairs[(code, sig)] = amps
    rules_rows: list[dict] = []
    for (code, sig), amps in sorted(bound_pairs.items()):
        amplifiers_payload = []
        evidence_payload: dict[str, dict] = {}
        any_valid = False
        for d, b, ev in amps:
            amplifiers_payload.append({
                "amplifier_type": d.amplifier_type,
                "amplifier_label": d.parent_label,
                "sub_type": d.sub_type,
                "sub_label": d.label,
                "action": b.action,
                "action_label": b.action_label,
                "base_bp": b.base_bp,
                "depth_factor": cfg.AMP_DEPTH_FACTOR,
                "depth_cap": cfg.AMP_DEPTH_CAP,
                "bar_def": AMPLIFIER_BAR_DEFS[
                    (d.amplifier_type, d.sub_type)],
            })
            if ev:
                # nested per the two-level axis: type -> sub -> block
                evidence_payload.setdefault(
                    str(d.amplifier_type), {})[str(d.sub_type)] = ev
                any_valid = any_valid or bool(ev.get("is_valid"))
        rules_rows.append({
            "code": code,
            "sec_type": sec_type_code,
            "signal_type": sig,
            "rule_params": json.dumps({
                "amplifiers": amplifiers_payload,
                "cap_bp": cfg.AMP_CAP_BP,
                "evidence": evidence_payload,
            }),
            "is_valid": any_valid,
            "rule_entry": RULE_ENTRY,
        })
    return rules_rows, history_rows


async def run_amplification(conn, sec_type: str, codes: list[str],
                            force: bool = False) -> tuple[int, int]:
    """Run the tier for one (sec_type, codes) scope; write both tables.
    Returns (n_rules, n_history) rows written."""
    if not codes:
        logger.info("[amplification] no codes — skip")
        return 0, 0
    sec_type_code = int(SEC_TYPE_CODE[sec_type])

    # incremental skip (before the build): the source horizon
    horizon_row = await conn.fetchrow(
        "SELECT max(date) AS horizon FROM "
        f"{_SOURCE_TABLES[sec_type]} WHERE close IS NOT NULL"
    )
    horizon = horizon_row["horizon"] if horizon_row else None
    if not force and horizon is not None \
            and await _is_current(conn, sec_type_code, horizon):
        logger.info(f"[amplification] {sec_type}: rules scope live and "
                    f"history at the source horizon ({horizon}) — skip")
        return 0, 0

    # 1. the ohlcv_ext build (returns the whole universe's anatomy) +
    #    the margin anatomy attach (the 'margin' parent type's source —
    #    codes without margin data keep NULL columns, no margin days)
    #    + the news anatomy attach (the 'news' parent type's source —
    #    the market state panel merged per DATE, NULL outside coverage)
    builder = OhlcvExtBuilder(conn, sec_type, codes, force=force)
    anatomy = await builder.build()
    if anatomy.empty:
        logger.warning("[amplification] empty anatomy frame — nothing "
                       "to do")
        return 0, 0
    anatomy = await MarginAnatomy(conn, sec_type, codes).attach(anatomy)
    anatomy = await NewsAnatomy(conn, sec_type, codes).attach(anatomy)

    # 2. the amplifier days (the extra anatomy facts ride along for the
    # history rows' amplifier_params payload)
    amp_days = AmplifierEngine(anatomy).run()
    if amp_days.empty:
        logger.info("[amplification] no amplifier days in scope")
        return 0, 0
    amp_days = amp_days.merge(
        anatomy[["code", "date"] + _PARAM_FACT_COLUMNS],
        on=["code", "date"], how="left",
    )
    logger.info(f"      amplifiers: {len(amp_days):,} amplifier days "
                f"detected")

    # 3. the per-binding evidence
    ev = AmplificationEvidence(anatomy)
    events = await ev.load_events(conn, sec_type_code,
                                  _BOUND_SIGNAL_TYPES, codes)
    evidence = ev.measure(amp_days, events)

    # the per-day fired lookup: (code, signal_type, action, date) ->
    # that day's fired-event count (the history rows' `fired` flag)
    fired_map: dict[tuple[str, int, int, dt.date], int] = {}
    if not events.empty:
        ev_np = {
            c: host_array(events[c].to_numpy(dtype="object"))
            for c in ("code",)
        }
        ev_dates = host_array(events["date"]).astype("datetime64[D]")
        ev_int = {
            c: host_array(events[c].to_numpy(dtype="float64",
                                             na_value=np.nan))
            for c in ("signal_type", "action", "fired_n")
        }
        for i in range(len(events)):
            key = (str(ev_np["code"][i]), int(ev_int["signal_type"][i]),
                   int(ev_int["action"][i]), ev_dates[i].item())
            fired_map[key] = fired_map.get(key, 0) + int(
                ev_int["fired_n"][i])

    # 4. the rows
    fired_pairs = {(c, s) for (c, s, _a, _d) in fired_map}
    rules_rows, history_rows = _materialize(
        amp_days, evidence, fired_map, fired_pairs, sec_type_code)

    # ---- writes --------------------------------------------------------
    # rules: current-state — wipe the sec_type scope, reinsert
    await chunked_purge_async(
        conn, RULES_TABLE, where_sql="sec_type = $1",
        params=(sec_type_code,),
    )
    if rules_rows:
        await copy_insert_async(conn, RULES_TABLE, rules_rows)

    # history: force purges the scope; incremental split past max date
    if force:
        await chunked_purge_async(
            conn, HISTORY_TABLE, where_sql="sec_type = $1",
            params=(sec_type_code,),
        )
        await batched_copy_by_key_async(
            conn, HISTORY_TABLE, history_rows, key="code",
            label="amp_history",
        )
    else:
        await copy_or_upsert_split_async(
            conn, HISTORY_TABLE, history_rows,
            key_columns=["code", "sec_type", "amplifier_type",
                         "sub_type", "date"],
        )

    logger.info(f"[amplification] {sec_type}: {len(rules_rows)} rules "
                f"rows, {len(history_rows):,} history rows written "
                f"({sum(1 for r in rules_rows if r['is_valid'])} rule "
                f"configs evidence-valid)")
    return len(rules_rows), len(history_rows)


# the source-horizon lookup table (the base stats tables — the same
# rows the builder admits via close IS NOT NULL)
_SOURCE_TABLES = {
    "index": "stats.index_basic_stats",
    "etf": "stats.etf_basic_stats",
    "stock": "stats.stock_basic_stats",
}
