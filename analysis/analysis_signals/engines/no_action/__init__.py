"""The NO-ACTION overlay tier (analysis.analysis_signals.engines.
no_action — 2026-11-18): per-family trigger-time INVALIDATION rules
plus the cross-family FATIGUE filter, computed INDEPENDENTLY of the
positive-confidence path and written to the tier's OWN table
(analysis_signals.no_action_signals — see docs/
no_action_module_design_2026-11-18.md).

The registry maps family labels to their rule classes (dispatch,
never if/else); a family absent from the registry gets the FATIGUE
filter only. The caller (run.run_family) invokes ``evaluate_family``
AFTER the positive entry loop for STANDALOLE families only (the
portfolio families are out of scope v1) and owns the writes — this
package computes and materializes rows, nothing else.

Returned rows ride the fired row's own identity + values (OVERLAY:
a no-action row never exists without its positive twin); the
accompanying audit digest re-measures each rule's marked vs kept
populations on the frame's forward blend (the conservatism
contract's run-time teeth — logged by the caller every run).
"""
from __future__ import annotations

import logging

from _common.df_utils import host_array, safe_columns

from analysis.analysis_signals import config as cfg
from analysis.analysis_signals.engines.no_action.base import (
    NoActionCtx,
    NoActionRule,
    fired_day_key,
    materialize,
    positive_fired_rows,
)
from analysis.analysis_signals.engines.no_action.boll_rules import (
    BollBandwalkSell,
    BollSqueezeBreakdown,
)
from analysis.analysis_signals.engines.no_action.fatigue import (
    FatigueFilter,
)
from analysis.analysis_signals.engines.no_action.macd_rules import (
    MacdChop,
)
from analysis.analysis_signals.engines.no_action.ma_rules import (
    MaWhipsaw,
)
from analysis.analysis_signals.engines.no_action.rebound_rules import (
    ReboundPeDeadzone,
)
from analysis.analysis_signals.engines.no_action.rsi_rules import (
    RsiBandwalkSell,
)

logger = logging.getLogger(__name__)

#: family label -> its invalidation rule classes (the FATIGUE
#: filter joins every standalone family implicitly — cross-family)
NO_ACTION_RULES: dict[str, list[type[NoActionRule]]] = {
    "mov_rsi": [RsiBandwalkSell],
    "bollinger": [BollSqueezeBreakdown, BollBandwalkSell],
    "ma_cross": [MaWhipsaw],
    "rebound": [ReboundPeDeadzone],
    "macd": [MacdChop],
}


def family_rules(label: str) -> list[NoActionRule]:
    """The family's ACTIVE rule instances — the registry filtered by
    the calibration verdict (config NO_ACTION_ACTIVE_RULES: a rule
    ships only with measured separation + rarity; the retired ones
    stay implemented for the vocabulary) + the fatigue filter when
    its own gate is on. Empty when the tier is disabled."""
    if not cfg.NO_ACTION_ENABLED:
        return []
    active = set(cfg.NO_ACTION_ACTIVE_RULES)
    rules: list[NoActionRule] = [cls() for cls
                                 in NO_ACTION_RULES.get(label, [])
                                 if cls.REASON in active]
    if cfg.NO_ACTION_FATIGUE_ENABLED:
        rules.append(FatigueFilter())
    return rules


def evaluate_family(label: str, frame, history_rows: list[dict]
                    ) -> tuple[list[dict], dict]:
    """The per-family no-action evaluation — the WHOLE tier in one
    call, isolated from the positive path (frame + fired rows in,
    rows + audit digest out; the caller owns the writes).

    Returns ``(no_action_rows, audit_digest)``. The digest maps each
    rule's reason to the marked-vs-kept forward-blend contrast (the
    fixed blend, sign-aligned by the declined action — the same
    measurement contract the families use; blend-unrealized newest
    rows are skipped)."""
    rules = family_rules(label)
    if not rules:
        return [], {}
    fired = positive_fired_rows(history_rows)
    if not fired or frame is None or frame.empty:
        return [], {}
    ctx = NoActionCtx(frame)
    for r in fired:
        ctx.fired_days[r["action"]].add(
            fired_day_key(r["code"], r["date"]))
    try:
        for rule in rules:
            rule.attach(frame, ctx)
        cols = [c for rule in rules for c in rule.CONTEXT_COLS]
        arrays = {c: host_array(frame[c]) for c in cols}
        rows: list[dict] = []
        for r in fired:
            i = ctx.pos_index.get(fired_day_key(r["code"], r["date"]))
            if i is None:
                # a fired row outside the prepared frame (a sourced
                # tier leftover) — never mark what we cannot see
                continue
            ctxv = {c: arrays[c][i] for c in cols}
            for rule in rules:
                if rule.matches(r, ctxv):
                    rows.append(materialize(r, rule))
        digest = _audit_digest(frame, fired, rows, ctx)
        return rows, digest
    finally:
        # every helper column this tier added starts with _na_ —
        # leave the caller's frame exactly as it arrived (the
        # isolation contract extends to the in-memory frame)
        added = [c for c in safe_columns(frame) if c.startswith("_na_")]
        if added:
            frame.drop(columns=added, inplace=True, errors="ignore")


def _audit_digest(frame, fired: list[dict], rows: list[dict],
                  ctx: NoActionCtx) -> dict:
    """Each active rule's marked-vs-kept forward-blend contrast —
    the run-time separation check (a rule whose marked population
    stops measuring worse than its kept one is retired at the next
    calibration pass; the measurement decides, not taste)."""
    if "_blend" not in set(safe_columns(frame)):
        return {}
    blend = host_array(frame["_blend"])
    # sign-aligned by the FIRED row's own action (the families'
    # measurement contract: a sell's forward blend is negated)
    marked: dict[str, set[tuple[str, int]]] = {}
    for r in rows:
        marked.setdefault(
            str(r["rule_params"]["reason"]), set(),
        ).add(fired_day_key(r["code"], r["date"]))
    acc = {reason: {"m": 0.0, "mn": 0, "k": 0.0, "kn": 0}
           for reason in marked}
    for r in fired:
        i = ctx.pos_index.get(fired_day_key(r["code"], r["date"]))
        if i is None:
            continue
        b = blend[i]
        if b is None or b != b:  # NaN — the 20d future not realized
            continue
        val = b * (1.0 if r["action"] == 0 else -1.0)
        hit = [reason for reason, keys in marked.items()
               if fired_day_key(r["code"], r["date"]) in keys]
        for reason in acc:
            if reason in hit:
                acc[reason]["m"] += val
                acc[reason]["mn"] += 1
            else:
                acc[reason]["k"] += val
                acc[reason]["kn"] += 1
    return {
        reason: {
            "marked_n": v["mn"],
            "marked_dir_ave_pct": round(v["m"] / v["mn"] * 100.0, 3)
            if v["mn"] else None,
            "kept_n": v["kn"],
            "kept_dir_ave_pct": round(v["k"] / v["kn"] * 100.0, 3)
            if v["kn"] else None,
        }
        for reason, v in acc.items()
    }


__all__ = ["NO_ACTION_RULES", "evaluate_family", "family_rules"]
