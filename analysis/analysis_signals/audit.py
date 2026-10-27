"""The post-write AUDIT GATE of the signals pipeline — the in-run
cross-tier consistency checks, logged every run (violation lines +
a summary count; the run NEVER aborts on them, the log is the alarm).

WHY IT EXISTS (2026-10-26, user-directed after the portfolio cell-signals
side-leak): the UI's cell-signals table had rendered SELL rows under a
BUY witness because ``mapped_signals`` names the sub type only and mov_rsi
registers the same sub type on BOTH sides — the engine's JSON payloads and
the UI's resolution had drifted apart with no run-time alarm. The audit
re-derives EVERY cross-tier contract from the rows the run just emitted
and the tables it just wrote, so the next writer drift is a loud log line
on the next run, not a UI bug report:

1. SIDE ALIGNMENT — every witness record's ``action`` vs the registered
   rows its ``mapped_signals`` names at the witness's own code (a name
   resolving only to opposite-side rows is the 2026-10-26 bug class; a
   name resolving to nothing is a dangling reference).
2. IDS REACHABILITY — every mapped registered row rides the portfolio
   rule's ``signal_rule_ids`` (the UI fetches cells through the ids array
   ONLY; a mapped cell outside it is invisible).
3. IDS INTEGRITY — every ``signal_rule_ids`` entry exists among the
   run's emitted rule rows (a dangling id is a silent hole in the panel).
4. WITNESS SHAPE + ROLE/ACTION PAIRING — the stored witness records
   carry the full record shape (sub_type/code/role/condition/bar/action)
   with decodable action words, and the *_buy_leg / *_sell_leg sub types
   pair with their own action word (the spread-exit flip bug class).
5. WITNESS COVERAGE — the witness records tile the construction block
   exactly ({market} + both legs per the coordination's witnesses list);
   a dropped or duplicated witness curve is a coverage hole.
6. EVENT MEMBER INVARIANT — every portfolio event carries >= 2 member
   records on >= 2 codes (the 2026-10-22 member-action invariant), every
   member action decodes, and the family semantics hold (trio events:
   every member shares the event's action; spread events: one buy + one
   sell — a same-side pair means the role flip drifted).
7. ORPHAN ROWS — after the code-scoped wipes, no rows of the run's
   signal types / tiers linger at codes OUTSIDE the run's universe (the
   2026-10-25 universe-shrink incident: pair codes the data-driven
   offsets dropped left orphan rules + history behind; the recurrence
   risk was flagged then — this check is the standing catch).
8. VOCABULARY — every emitted action / trend / regime / signal type /
   sub type smallint code decodes in the static.label_dim mirrors
   (an undecodable code renders as a raw number in the UI end-to-end).
9. RULES<->HISTORY LINKAGE — every emitted history event's cell key
   exists among the emitted rule cells (an event whose cell did not
   emit this run silently vanishes from the history panel).
10. PERFORMANCE LINKAGE — every emitted rule row carries its dated
    performance snapshot (a rule without evidence renders blank).

Checks 1-6 run on the in-memory rows (cheap dict/set passes); 7 queries
the two tables post-write. All functions take plain dicts as written by
run_family (rule_params already JSON-dumped is fine — parsed back).
"""
from __future__ import annotations

import json
import logging

from _common.labels import (
    ACTION_LABEL,
    REGIME_LABEL,
    SEC_TYPE_CODE,
    SIGNAL_SUB_TYPE_LABEL,
    SIGNAL_TYPE_LABEL,
    TREND_LABEL,
)

logger = logging.getLogger(__name__)

_ACTION_WORDS = set(ACTION_LABEL.values())

_TIER_SQL = {True: " AND is_in_portfolio",
             False: " AND NOT is_in_portfolio",
             None: ""}


def _params(row: dict) -> dict:
    """The row's rule_params as a dict — the writer may already have
    JSON-dumped it for COPY (run_family dumps before the audit runs)."""
    rp = row.get("rule_params")
    if isinstance(rp, str):
        try:
            return json.loads(rp) or {}
        except (TypeError, ValueError):
            return {}
    return rp if isinstance(rp, dict) else {}


def _violations(label: str, tag: str, rows: list[str]) -> None:
    for line in rows:
        logger.warning(f"[audit] {label} · {tag}: {line}")


# ---------------------------------------------------------------- 8/9/10
def audit_family_rows(label: str, rules_rows: list[dict],
                      history_rows: list[dict],
                      perf_by_id: dict[int, dict]) -> int:
    """The GENERIC checks over one family run's emitted rows (checks 8,
    9, 10 + the rule_params shape). Returns the violation count."""
    bad: list[str] = []
    rule_cells: set[tuple] = set()
    for r in rules_rows:
        rid = r.get("signal_rule_id")
        where = f"{r.get('code')}/{r.get('signal_sub_type')}"
        if r.get("action") not in ACTION_LABEL:
            bad.append(f"{where}: undecodable action code {r.get('action')}")
        if r.get("trend") not in TREND_LABEL:
            bad.append(f"{where}: undecodable trend code {r.get('trend')}")
        if r.get("regime_state") not in REGIME_LABEL:
            bad.append(f"{where}: undecodable regime code "
                       f"{r.get('regime_state')}")
        if r.get("signal_type") not in SIGNAL_TYPE_LABEL:
            bad.append(f"{where}: undecodable signal_type code "
                       f"{r.get('signal_type')}")
        if r.get("signal_sub_type") not in SIGNAL_SUB_TYPE_LABEL:
            bad.append(f"{where}: undecodable sub_type code "
                       f"{r.get('signal_sub_type')}")
        bar_def = _params(r).get("bar_def")
        if not (isinstance(bar_def, dict) and bar_def.get("kind")):
            bad.append(f"{where}: rule_params.bar_def missing its kind")
        if rid not in perf_by_id:
            bad.append(f"{where}: no performance snapshot emitted")
        rule_cells.add((r.get("code"), r.get("signal_type"),
                        r.get("signal_sub_type"), r.get("action"),
                        r.get("is_in_portfolio"),
                        r.get("is_pe_boosted"),
                        r.get("is_ohlcv_boosted")))
    for h in history_rows:
        key = (h.get("code"), h.get("signal_type"),
               h.get("signal_sub_type"), h.get("action"),
               h.get("is_in_portfolio"), h.get("is_pe_boosted"),
               h.get("is_ohlcv_boosted"))
        if key not in rule_cells:
            bad.append(f"history {h.get('code')}@{h.get('date')}/"
                       f"{h.get('signal_sub_type')}: its rule cell did "
                       f"not emit this run")
        conf = h.get("confidence")
        if conf is None:
            bad.append(f"history {h.get('code')}@{h.get('date')}/"
                       f"{h.get('signal_sub_type')}: null confidence")
    _violations(label, "family rows", bad)
    return len(bad)


# ------------------------------------------------------- 1-5 + 6 (events)
def audit_portfolio_groups(label: str, groups: dict[tuple, dict],
                           rules_rows: list[dict],
                           portfolio_events: list[dict]) -> int:
    """The PORTFOLIO-tier checks over the built groups (checks 1-6).
    ``groups`` — the (code, sub) → group dict from _write_portfolio_tables
    (each: frag / ids); ``portfolio_events`` — the peeled event rows
    (member_signals already JSON-dumped — parsed back here). Returns the
    violation count."""
    bad: list[str] = []
    emitted_ids = {int(r["signal_rule_id"]) for r in rules_rows}
    # the REGISTERED component rows indexed by (code, sub-type label)
    comp_idx: dict[tuple[str, str], list[dict]] = {}
    for r in rules_rows:
        if _params(r).get("portfolio_role") != "component":
            continue
        name = SIGNAL_SUB_TYPE_LABEL.get(int(r["signal_sub_type"]))
        comp_idx.setdefault((r["code"], name), []).append(r)
    for (code, sub), g in groups.items():
        where = f"{code}/sub{sub}"
        ids_set = set(int(i) for i in g["ids"])
        # 3. ids integrity — every id is an emitted rule row
        for rid in ids_set:
            if rid not in emitted_ids:
                bad.append(f"{where}: signal_rule_ids carries dangling id "
                           f"{rid}")
        frag = g["frag"] or {}
        port = frag.get("portfolio") or {}
        legs = port.get("legs") or []
        role_code = {"market": port.get("market"),
                     "leg_a": legs[0] if legs else None,
                     "leg_b": legs[1] if len(legs) > 1 else None}
        for sub_name, spec in (frag.get("subs") or {}).items():
            recs = (spec or {}).get("cell_signals") or []
            if not isinstance(recs, list):
                recs = []
            # 5. witness coverage — the records tile the witnesses list
            want = (spec.get("coordination") or {}).get("witnesses") or []
            want_codes = {role_code[w] for w in want if w in role_code}
            got_codes = {w.get("code") for w in recs
                         if isinstance(w, dict)}
            if want_codes and got_codes != want_codes:
                bad.append(f"{where}/{sub_name}: witness codes "
                           f"{sorted(map(str, got_codes))} != construction "
                           f"{sorted(map(str, want_codes))}")
            for w in recs:
                if not isinstance(w, dict):
                    bad.append(f"{where}/{sub_name}: malformed witness "
                               f"record {w!r}")
                    continue
                wid = f"{where}/{sub_name}/{w.get('sub_type')}@{w.get('code')}"
                # 4. shape + role/action pairing
                for k in ("sub_type", "code", "role", "condition", "bar"):
                    if not isinstance(w.get(k), str) or not w.get(k):
                        bad.append(f"{wid}: witness record misses {k}")
                act = w.get("action")
                if act not in _ACTION_WORDS:
                    bad.append(f"{wid}: action {act!r} not a label word")
                st = w.get("sub_type") or ""
                if st.endswith("_buy_leg") and act != "buy":
                    bad.append(f"{wid}: buy-leg sub type carries {act!r}")
                if st.endswith("_sell_leg") and act != "sell":
                    bad.append(f"{wid}: sell-leg sub type carries {act!r}")
                # 1. side alignment + 2. ids reachability
                for name in (w.get("mapped_signals") or []):
                    rows_at = comp_idx.get((w.get("code"), name), [])
                    same = [r for r in rows_at
                            if ACTION_LABEL.get(int(r["action"])) == act]
                    if not rows_at:
                        bad.append(f"{wid}: mapped name {name} resolves "
                                   f"to NO registered row")
                    elif not same:
                        sides = sorted({ACTION_LABEL.get(int(r["action"]))
                                        for r in rows_at})
                        bad.append(f"{wid}: mapped name {name} resolves "
                                   f"only to the {sides} side (witness "
                                   f"is {act})")
                    for r in same:
                        if int(r["signal_rule_id"]) not in ids_set:
                            bad.append(f"{wid}: mapped {name} row "
                                       f"{r['signal_rule_id']} not in "
                                       f"signal_rule_ids")
    # 6. event member invariant + family action semantics
    for ev in portfolio_events:
        sub_word = SIGNAL_SUB_TYPE_LABEL.get(int(ev["signal_sub_type"]), "")
        where = f"event {ev.get('code')}@{ev.get('date')}/{sub_word}"
        raw = ev.get("member_signals")
        members = raw if isinstance(raw, list) else (
            json.loads(raw) if isinstance(raw, str) else [])
        if len(members) < 2:
            bad.append(f"{where}: {len(members)} member(s) — the >= 2 "
                       f"member invariant")
        codes = {m.get("code") for m in members}
        if len(codes) < 2:
            bad.append(f"{where}: members on {len(codes)} code(s) — the "
                       f">= 2 codes invariant")
        acts = {ACTION_LABEL.get(int(m["action"]))
                for m in members if isinstance(m, dict)}
        if None in acts:
            bad.append(f"{where}: a member action code does not decode")
        ev_act = ACTION_LABEL.get(int(ev.get("action", -1)))
        if sub_word.endswith("_trio") and ev_act is not None:
            stray = acts - {ev_act}
            if stray:
                bad.append(f"{where}: members carry {sorted(map(str, stray))}"
                           f" — the trio wash is same-side ({ev_act})")
        if sub_word.endswith("_spread") and acts != {"buy", "sell"} \
                and acts <= _ACTION_WORDS and acts:
            bad.append(f"{where}: member sides {sorted(map(str, acts))} — "
                       f"the spread is one buy + one sell")
    _violations(label, "portfolio", bad)
    return len(bad)


# ------------------------------------------------------------------ 7
async def audit_orphan_rows(conn, sec_type: str, label: str,
                            scopes: list[tuple[int, list[str], bool | None]],
                            ) -> int:
    """Rows of the run's (signal_type, tier) scopes lingering at codes
    OUTSIDE the scope's codes — the universe-shrink incident class (the
    code-scoped wipes cannot see codes the universe dropped). Queries
    both tables post-write; returns the violation count."""
    st_code = SEC_TYPE_CODE[sec_type]
    bad: list[str] = []
    for wipe_type, wipe_codes, tier in scopes:
        if not wipe_codes:
            continue
        tier_sql = _TIER_SQL.get(tier, "")
        for table, tname in (("analysis_signals.signal_rules", "rules"),
                             ("analysis_signals.history_signals",
                              "history")):
            rows = await conn.fetch(
                f"SELECT code, count(*) AS n FROM {table} "
                f"WHERE sec_type = $1 AND signal_type = $2{tier_sql} "
                f"AND NOT (code = ANY($3)) GROUP BY code",
                st_code, wipe_type, sorted(wipe_codes),
            )
            for r in rows:
                bad.append(f"{tname} type {wipe_type} tier {tier}: "
                           f"{r['n']} orphan rows at {r['code']} — "
                           f"outside the run universe (cleanup needed)")
    _violations(label, "orphans", bad)
    return len(bad)


__all__ = ["audit_family_rows", "audit_portfolio_groups",
           "audit_orphan_rows"]
