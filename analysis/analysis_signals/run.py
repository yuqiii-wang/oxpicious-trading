"""The signal rules pipeline (analysis.analysis_signals.run) —
orchestrates the families' RuleEntry engines over a sec_type's
universe and writes the tables of the redesigned tier:

  signal_rules    current-state: the family's rule scope for the
                  sec_type is DELETED then freshly inserted (cells that
                  stopped passing the gate must not linger); every row
                  carries a fresh surrogate signal_rule_id from
                  signal_rule_id_seq (identifies the row EMISSION —
                  the scope churns every run); rule_params carry the
                  CONSTANT rule only (the 2026-10-19 split); the
                  is_in_portfolio flag (2026-10-20) marks the cells a
                  portfolio group claims through its signal_rule_ids
  history_signal_performance  the cell's OWN MEASURED evidence as a
                  DATED daily snapshot (the measure block — n /
                  dir_ave / std / t / hit — + the is_in_portfolio
                  flag; slimmed 2026-10-20: the pooled tier / gate
                  digest / boost conf / cascade never ride the cell
                  snapshot), peeled from the rows' _performance
                  payloads and upserted on the PK + date; NEVER purged,
                  not even under --force (past snapshots are the
                  point: a cell's evidence evolution, graduation and
                  death survive the rules tier's per-run wipes)
  portfolio_signal_rules (the PORTFOLIO families — hedge_pairs, per
                  the FAMILY_PORTFOLIO_TYPE registry): ONE ROW PER
                  PORTFOLIO (code, portfolio_type — the sub_type axis
                  dropped 2026-10-20) carrying ALL the portfolio's
                  member rule ids in signal_rule_ids BIGINT[] + the
                  CONSTANT portfolio construction in rule_params JSONB
                  (the shared blocks + the per-sub coordination specs
                  under subs — the orchestrating rows'
                  _portfolio_rule_params fragments, merged per code);
                  same scope, wiped and rewritten in the same run
  history_portfolio_signal_performance  the PORTFOLIO-LEVEL AUDIT
                  (the portfolio families, renamed 2026-10-20): one
                  DATED daily snapshot per portfolio — WHAT THE
                  PORTFOLIO TOOK (the hedge type's claim: max
                  drawdown reduction — the pair value's own max
                  drawdown vs the legs' standalone), the
                  orchestrating cells' combined coordinated-event dd
                  lens, AND the COMBINED STATS of the individual
                  in-portfolio signals (pooled — the member cells'
                  own measures meta-combined) — peeled from the
                  orchestrating rows' _portfolio_performance
                  fragments + the members' _performance measures,
                  merged per code, upserted on the PK + date; NEVER
                  purged (the history_signal_performance lifecycle)
  portfolio_history_signals (the portfolio families, 2026-10-21):
                  the PORTFOLIO tier's EVENT history — one row per
                  coordinated portfolio event (every composite /
                  pair-native history row of the run) with THE EVENT
                  DATE and the member_signals JSONB — the sourced
                  component CELL EVENTS that triggered it (the
                  engines' _portfolio_event_members stamps; the
                  components' own history stays dropped from
                  history_signals). Same lifecycle as history_signals:
                  --force purges the (sec_type, portfolio_type) tier
                  and re-emits; incremental upserts past max(date)
  history_signals force: the scope is purged then COPY'd (conflict
                  free); incremental: copy-or-upsert split past the
                  table's max date (idempotent re-emission)
  trend_days      the prepared frame's per-(code, date) TREND
                  classification into analysis.trend_days (the
                  consolidated analysis.trends component's store,
                  family-agnostic; written by the first family run
                  whose data outdates the store)

Incremental default: a family is skipped when every requested code's
latest history row already sits at the family's data horizon AND the
rules scope is non-empty (the reads filter on the target tables'
primary keys).
"""
from __future__ import annotations

import json
import logging

from _common.db_commons import (
    chunked_purge_async,
    copy_insert_async,
    copy_or_upsert_split_async,
)
from _common.df_utils import host_array
from _common.labels import (
    PORTFOLIO_TYPE_CODE,
    SEC_TYPE_CODE,
    SIGNAL_SUB_TYPE_LABEL,
    SIGNAL_TYPE_CODE,
)

from analysis.analysis_signals.engines.signal_families import (
    FAMILY_PORTFOLIO_TYPE,
    SIGNAL_FAMILY_ENTRIES,
)

logger = logging.getLogger(__name__)

RULES_TABLE = "analysis_signals.signal_rules"
HISTORY_TABLE = "analysis_signals.history_signals"
TREND_TABLE = "analysis.trend_days"
PORTFOLIO_TABLE = "analysis_signals.portfolio_signal_rules"
PORTFOLIO_PERF_TABLE = \
    "analysis_signals.history_portfolio_signal_performance"
PORTFOLIO_HISTORY_TABLE = "analysis_signals.portfolio_history_signals"
PERF_TABLE = "analysis_signals.history_signal_performance"
RULE_ID_SEQ = "analysis_signals.signal_rule_id_seq"
RULES_PK = ["code", "sec_type", "signal_type", "signal_sub_type",
            "trend", "regime_state", "action"]
HISTORY_PK = ["code", "sec_type", "signal_type", "signal_sub_type",
              "is_ohlcv_boosted", "date", "time"]
PERF_PK = ["code", "sec_type", "signal_type", "signal_sub_type",
           "trend", "regime_state", "action", "date"]
PORTFOLIO_PERF_PK = ["code", "portfolio_type", "date"]
PORTFOLIO_HISTORY_PK = ["code", "sec_type", "portfolio_type",
                        "signal_type", "signal_sub_type", "date", "time"]


def _combine_member_stats(members: list[dict]) -> dict | None:
    """The COMBINED STATS of the individual in-portfolio signals
    (history_portfolio_signal_performance.pooled, 2026-10-20's
    cell-level redesign): the member cells' OWN measured evidence —
    their _performance measure blocks — meta-combined into ONE
    population record: the member-cell count, the total episode count,
    the n-weighted mean dir_ave / hit, and the t-stat of the combined
    moments (per-cell n / dir_ave / dir_std pooled: total scatter =
    Σ n_i·(std_i² + (dir_i − dir̄)²)). Each member keeps its OWN
    unified dir_ave test — this block is the portfolio tier's combined
    RECORD, never a cell gate. None on portfolios whose members
    measured nothing."""
    ms = [m for m in members if m]
    if not ms:
        return None
    n_total = sum(int(m["n_episodes"]) for m in ms)
    if n_total <= 0:
        return None
    dir_ave = sum(int(m["n_episodes"]) * float(m["dir_ave_pct"])
                  for m in ms) / n_total
    hits = [(int(m["n_episodes"]), m["hit_rate"]) for m in ms
            if m.get("hit_rate") is not None]
    hit = (sum(n * float(h) for n, h in hits) / sum(n for n, _ in hits)
           if hits else None)
    # the combined moments: cells with a std contribute their scatter
    # AROUND the combined mean; std-less members contribute only the
    # between-cell displacement of their mean (their within scatter
    # is unknown — the honest conservative omission)
    scatter = 0.0
    for m in ms:
        n_i = int(m["n_episodes"])
        std_i = m.get("dir_std_pct")
        var_i = float(std_i) ** 2 if std_i is not None else 0.0
        scatter += n_i * (var_i
                          + (float(m["dir_ave_pct"]) - dir_ave) ** 2)
    t_stat = None
    if scatter > 0.0:
        se = (scatter / n_total / n_total) ** 0.5
        t_stat = round(dir_ave / se, 4)
    return {
        "n_cells": len(ms),
        "n_episodes": n_total,
        "dir_ave_pct": round(dir_ave, 4),
        "t_stat": t_stat,
        "hit_rate": round(hit, 4) if hit is not None else None,
    }


def _combine_dd(fragments: list[dict | None]) -> dict | None:
    """The portfolio's COORDINATED-EVENT dd lens: the ORCHESTRATING
    cells' own dd blocks combined at the portfolio level — the
    n-weighted mean of the mean/p10 forward troughs, the SHALLOWEST
    baseline (the most conservative reading across the response
    paths), and the total trigger count. None where no fragment
    measured a lens."""
    vals = [f for f in fragments if isinstance(f, dict)]
    if not vals:
        return None
    n_total = sum(int(v.get("n") or 0) for v in vals)
    if n_total <= 0:
        return None
    out: dict = {"n": n_total}

    def _weighted(key: str) -> float | None:
        legs = [(int(v.get("n") or 0), v[key]) for v in vals
                if v.get(key) is not None]
        return (sum(n * float(x) for n, x in legs)
                / sum(n for n, _ in legs) if legs else None)

    for key in ("min20_pct", "p10_pct"):
        w = _weighted(key)
        out[key] = round(w, 4) if w is not None else None
    baselines = [float(v["baseline_pct"]) for v in vals
                 if v.get("baseline_pct") is not None]
    out["baseline_pct"] = round(max(baselines), 4) if baselines else None
    return out

_BASE_STATS_TABLE = {"index": "stats.index_basic_stats",
                     "etf": "stats.etf_basic_stats",
                     "stock": "stats.stock_basic_stats"}


def _source_horizon_sql(label: str, sec_type: str) -> tuple[str, tuple]:
    """(sql, params): per-code max date in the family's VALUE SOURCE,
    scoped to the requested codes — the data horizon the incremental
    skip compares against (sec_type-wide maxima would never let a
    scoped run skip)."""
    if label == "mov_rsi":
        return ("SELECT code, max(date) AS latest FROM "
                "analysis.mov_ave_rsi WHERE sec_type = $1 "
                "AND code = ANY($2) GROUP BY code",
                (SEC_TYPE_CODE[sec_type],))
    if label == "momentum":
        return ("SELECT code, max(date) AS latest FROM "
                "analysis.mov_ave_momentum WHERE sec_type = $1 "
                "AND code = ANY($2) GROUP BY code",
                (SEC_TYPE_CODE[sec_type],))
    if label == "ma_cross":
        return ("SELECT code, max(date) AS latest FROM "
                "analysis.mov_ave_spreads_detail WHERE sec_type = $1 "
                "AND code = ANY($2) GROUP BY code",
                (SEC_TYPE_CODE[sec_type],))
    if label == "options_wall":
        return ("SELECT underlying_code AS code, max(date) AS latest FROM "
                "analysis.options_spot_daily WHERE underlying_code = ANY($1)"
                " GROUP BY 1", ())
    if label == "options_vix":
        return ("SELECT underlying_code AS code, max(date) AS latest FROM "
                "analysis.options_vol_index WHERE underlying_code = ANY($1)"
                " GROUP BY 1", ())
    if label == "rf_break":
        return ("SELECT code, max(date) AS latest FROM "
                "analysis.mov_ave_spreads_roof_floor_estimates "
                "WHERE sec_type = $1 AND method IN (1, 5) "
                "AND code = ANY($2) GROUP BY code",
                (SEC_TYPE_CODE[sec_type],))
    if label == "hedge_pairs":
        # codes are composite 'A~B' pair ids — the horizon is the
        # freshest industry-composite close of either leg (pool 'all')
        return ("SELECT p.code, max(s.date) AS latest "
                "FROM unnest($1::text[]) AS p(code) "
                "JOIN stats.industry_basic_stats s "
                "  ON s.industry_id IN (split_part(p.code, '~', 1), "
                "                       split_part(p.code, '~', 2)) "
                " AND s.pool_size = 3 "
                "GROUP BY p.code", ())
    # bollinger: values are computed from the base OHLCV closes
    return (f"SELECT code, max(date) AS latest FROM "
            f"{_BASE_STATS_TABLE[sec_type]} WHERE close IS NOT NULL "
            f"AND code = ANY($1) GROUP BY code", ())


async def family_is_current(conn, sec_type: str, label: str,
                            codes: list[str]) -> bool:
    """Whether the family's rules were BUILT with data at least as new
    as the requested codes' value-source horizon. The rules scope is
    wiped and re-inserted every run, so its max(created_at) IS the last
    build time — comparing trigger dates would never skip (triggers
    are sparse; a quiet code would look stale forever)."""
    sql, leading = _source_horizon_sql(label, sec_type)
    rows = await conn.fetch(sql, *leading, sorted(codes))
    horizon = max((r["latest"] for r in rows), default=None)
    if horizon is None:
        return False  # no source data at all — nothing to be current on
    st = SEC_TYPE_CODE[sec_type]
    sig = SIGNAL_TYPE_CODE[label]
    rules_n, built = await conn.fetchrow(
        f"SELECT count(*), max(created_at::date) FROM {RULES_TABLE} "
        f"WHERE sec_type = $1 AND signal_type = $2",
        st, sig,
    )
    return (rules_n or 0) > 0 and built is not None and built >= horizon


async def _write_trend_days(conn, sec_type: str, frame,
                            force: bool) -> int:
    """The prepared frame's per-(code, date) TREND classification
    (analysis.trends.trend_state.attach_trend — the consolidated trends
    component, family-agnostic) into analysis.trend_days:
    purge + COPY fresh rows for the codes whose store LAGS the frame's
    data horizon (all codes on force). The check is PER-CODE so a
    universe growth (a new underlying joining the frame) is written even
    when the run's other codes are already fresh — a scope-wide max
    would let the veteran codes' freshness orphan the newcomer. The row
    materialization is the sanctioned host boundary (host_array →
    python records for COPY)."""
    codes_np = host_array(frame["code"])
    dates_np = host_array(frame["date"]).astype("datetime64[D]")
    trend_np = host_array(frame["trend"]).astype("int64")
    codes = sorted(set(codes_np.tolist()))
    py_dates = dates_np.tolist()
    st = SEC_TYPE_CODE[sec_type]
    horizon = max(py_dates)
    if force:
        stale = set(codes)
    else:
        stored = await conn.fetch(
            f"SELECT code, max(date) AS latest FROM {TREND_TABLE} "
            f"WHERE sec_type = $1 AND code = ANY($2) GROUP BY code",
            st, codes,
        )
        store_latest = {r["code"]: r["latest"] for r in stored}
        stale = {c for c in codes
                 if store_latest.get(c) is None or store_latest[c] < horizon}
        if not stale:
            return 0
    rows = [
        {"code": str(codes_np[i]), "sec_type": st,
         "date": py_dates[i], "trend": int(trend_np[i])}
        for i in range(len(py_dates)) if str(codes_np[i]) in stale
    ]
    await chunked_purge_async(
        conn, TREND_TABLE,
        where_sql="sec_type = $1 AND code = ANY($2)",
        params=(st, sorted(stale)), key_column="code",
    )
    if rows:
        await copy_insert_async(conn, TREND_TABLE, rows)
    return len(rows)


async def run_family(conn, label: str, sec_type: str, codes: list[str],
                     force: bool = False,
                     boost: bool | None = None) -> tuple[int, int]:
    """Run one family's entries over the shared prepared frame and
    write both tables. Returns (n_rules, n_history) rows written.
    ``boost`` (None = each entry's class default) forces the ohlcv
    boost off (the --no-boost A/B switch)."""
    st = SEC_TYPE_CODE[sec_type]
    if not codes:
        logger.info(f"[{label}] no codes for sec_type={sec_type} — skip")
        return 0, 0
    if not force and await family_is_current(conn, sec_type, label, codes):
        logger.info(f"[{label}] {sec_type}: all {len(codes)} codes at the "
                    f"data horizon with a live rules scope — skip")
        return 0, 0

    entries = SIGNAL_FAMILY_ENTRIES[label]
    entry0 = entries[0](conn, sec_type, codes)
    frame = await entry0.prepare_frame()
    rules_rows: list[dict] = []
    history_rows: list[dict] = []
    if frame.empty:
        logger.warning(f"[{label}] {sec_type}: empty input frame — nothing "
                       f"to do")
    else:
        for entry_cls in entries:
            entry = entry_cls(conn, sec_type, codes, boost=boost)
            r, h = entry.run(frame)
            r, h, boost_report = entry.finalize_boost(r, h)
            rules_rows.extend(r)
            history_rows.extend(h)
            boost_note = ""
            if boost_report.get("enabled"):
                sides = boost_report.get("sides", {})
                parts = []
                for n, s in sides.items():
                    if s.get("passed"):
                        parts.append(
                            f"{n}:PASS(g1 {s['g1_improv_bp']}bp,"
                            f"g2 {s['g2']['years_positive']})",
                        )
                    else:
                        reason = s.get("reason")
                        parts.append(f"{n}:FAIL({reason or 'gates'})")
                boost_note = (" · ohlcv-boost: " + ", ".join(parts))
            logger.info(f"[{label}] {entry.entry_name}: {len(r)} rule "
                        f"cells, {len(h)} history events{boost_note}")
        # composite-scope families (hedge_pairs' 'A~B' pair ids) keep
        # their codes OUT of the trends store — the pair portfolio's
        # own trend column still rides the frame (unused: the family
        # pools on both cell axes)
        if entry0.WRITES_TREND_DAYS:
            n_trend = await _write_trend_days(conn, sec_type, frame,
                                              force)
            if n_trend:
                logger.info(f"[{label}] {sec_type}: wrote {n_trend} "
                            f"trend days")

    # signal_rules: current-state — wipe THE RUN'S CODE SCOPES of the
    # family's signal_type(s), insert fresh. CODE-SCOPED (not the
    # whole (sec_type, signal_type) tier): concurrent writers of the
    # same signal_type at other codes never collide — the hedge_pairs
    # family's per-code components are REAL mov_rsi rows (type 0) at
    # the market + industry codes, which a mov_rsi family run must
    # not wipe; the family declares its scopes via rule_wipe_scopes
    # (hedge_pairs: its own type at the pair codes + type 0 at the
    # component codes)
    for wipe_type, wipe_codes in entry0.rule_wipe_scopes():
        await conn.execute(
            f"DELETE FROM {RULES_TABLE} "
            f"WHERE sec_type = $1 AND signal_type = $2 "
            f"AND code = ANY($3)",
            st, wipe_type, sorted(wipe_codes),
        )
    # engine portfolio-group tags, peeled BEFORE the COPY
    # (signal_rules has no such column — the dict keys drive the COPY
    # column list): the PORTFOLIO CODES the row joins (a NON-EMPTY
    # list = a member cell of those portfolios), an EMPTY list
    # (signal_rules only — the orchestrating composites), or None
    # (default: its own code — the pair-native self-members; only
    # meaningful inside the portfolio families). The _performance
    # payloads peel the same way — the cell's OWN measure block, the
    # dated history_signal_performance snapshot (the 2026-10-19 split,
    # slimmed 2026-10-20: the pooled tier / gate digest / boost conf /
    # cascade never ride the cell snapshot — the combined tiers live
    # on the portfolio tier)
    ptype_label = FAMILY_PORTFOLIO_TYPE.get(label)
    portfolio_groups: list[list[str] | None] = []
    perf_by_id: dict[int, dict] = {}
    # the portfolio tier's peels (2026-10-20's cell-level redesign):
    # the orchestrating rows' PER-SUB fragments — the CONSTANT
    # construction params + the dated audit payloads — MERGED here per
    # portfolio code (one row per portfolio; the sub axis is gone)
    portfolio_params: dict[str, dict] = {}
    portfolio_perf: dict[str, dict] = {}
    if rules_rows:
        # the surrogate rule ids (signal_rule_id): ONE batched nextval
        # draw stamped on the rows before COPY — the id identifies this
        # run's ROW EMISSION (the scope is wiped and re-inserted every
        # run), and portfolio_signal_rules joins it in the same run
        ids = await conn.fetch(
            f"SELECT nextval('{RULE_ID_SEQ}') AS id "
            f"FROM generate_series(1, $1::int)", len(rules_rows),
        )
        for row, rid in zip(rules_rows, ids):
            row["signal_rule_id"] = rid["id"]
            portfolio_groups.append(row.pop("_portfolio_groups", None))
            frag = row.pop("_portfolio_rule_params", None)
            if frag is not None:
                # the per-sub construction fragment: the shared
                # top-level blocks fold once per portfolio code, the
                # sub's own spec nests under rule_params.subs[label]
                sub_spec = frag.pop("sub")
                cur = portfolio_params.setdefault(row["code"], frag)
                cur.setdefault("subs", {})[
                    SIGNAL_SUB_TYPE_LABEL[row["signal_sub_type"]]
                ] = sub_spec
            pp = row.pop("_portfolio_performance", None)
            if pp is not None:
                cur = portfolio_perf.get(row["code"])
                if cur is None:
                    portfolio_perf[row["code"]] = {
                        "date": pp["date"],
                        "benefit": pp["benefit"],
                        "gate": pp["gate"],
                        # the merged row's verdict: the OR of the
                        # fragments' (each = its orchestrator's
                        # is_valid AND the portfolio gate)
                        "group_is_valid": bool(pp["group_is_valid"]),
                        "entries": {str(pp["rule_entry"])},
                        "dd_fragments": (
                            [pp["dd"]] if pp["dd"] is not None else []
                        ),
                    }
                else:
                    cur["group_is_valid"] = (
                        cur["group_is_valid"]
                        or bool(pp["group_is_valid"]))
                    cur["entries"].add(str(pp["rule_entry"]))
                    if pp["dd"] is not None:
                        cur["dd_fragments"].append(pp["dd"])
            perf = row.pop("_performance", None)
            if perf is not None:
                perf_by_id[int(rid["id"])] = perf

    # the PORTFOLIO MEMBERSHIP map (2026-10-20): one group per
    # (portfolio code, portfolio_type) — ALL of a pair's member ids in
    # the single array (the engine's pair-code tags; the portfolio
    # family's untagged rows default to their own code — the
    # pair-native self-members; non-portfolio families join nothing).
    # The member cells' is_in_portfolio flag derives from the arrays
    # (the cell IS a portfolio member iff its id rides one)
    groups: dict[str, list[int]] = {}
    for r, joins in zip(rules_rows, portfolio_groups):
        tags = joins if joins is not None \
            else ([r["code"]] if ptype_label is not None else [])
        for g_code in tags:
            groups.setdefault(g_code, []).append(r["signal_rule_id"])
    member_ids = {i for ids_ in groups.values() for i in ids_}
    if rules_rows:
        for row in rules_rows:
            row["is_in_portfolio"] = row["signal_rule_id"] in member_ids
        for row in rules_rows:  # JSONB rides COPY as JSON text
            row["rule_params"] = json.dumps(row["rule_params"])
        await copy_insert_async(conn, RULES_TABLE, rules_rows)

    # portfolio_signal_rules: the PORTFOLIO families' run's rules as
    # ONE ROW PER PORTFOLIO (code, portfolio_type) — the whole
    # portfolio's member ids in the single signal_rule_ids BIGINT[]
    # (across sub types; the orchestrating composites carry no
    # membership — the group IS the composite). Group membership is
    # ENGINE-TAGGED (``_portfolio_groups`` — the pair codes: the
    # hedge_pairs entry composites claim the winning tail's THREE
    # component BUY ids, the exits the market's / legs' SELL ids; an
    # EMPTY list means signal_rules only; an UNTAGGED row defaults to
    # its own code — the pair-native self-members). The SAME scope
    # lifecycle as the rules table (wiped and rewritten together, so a
    # portfolio never dangles when cells stop passing)
    if ptype_label is not None:
        ptype = PORTFOLIO_TYPE_CODE[ptype_label]
        await conn.execute(
            f"DELETE FROM {PORTFOLIO_TABLE} "
            f"WHERE sec_type = $1 AND portfolio_type = $2", st, ptype,
        )
        if groups:
            # the portfolios' is_valid / rule_entry columns (the
            # signal_rules conventions, 2026-10-20): the ORCHESTRATING
            # rows' merged payload carries them — the portfolio's
            # is_valid = ANY fragment's verdict (each = its
            # orchestrator's own is_valid AND the portfolio gate — the
            # conjunction that gates the member confidence cascade);
            # rule_entry names the writing entries, '|'-joined
            await copy_insert_async(conn, PORTFOLIO_TABLE, [
                {"code": code, "sec_type": st, "portfolio_type": ptype,
                 "signal_rule_ids": ids_,
                 "is_valid": bool(
                     portfolio_perf.get(code, {}).get("group_is_valid")),
                 "rule_entry": "|".join(sorted(
                     portfolio_perf.get(code, {}).get("entries") or [])),
                 # the CONSTANT portfolio construction (the merged
                 # per-sub fragments; JSONB rides COPY as JSON text —
                 # the rule_params convention)
                 "rule_params": json.dumps(
                     portfolio_params.get(code, {}))}
                for code, ids_ in sorted(groups.items())
            ])

        # history_portfolio_signal_performance: the run's PORTFOLIO
        # AUDIT snapshot per portfolio (what the portfolio took — the
        # hedge type's max drawdown reduction; the orchestrating
        # cells' combined dd lens; and the COMBINED STATS of the
        # individual in-portfolio signals — the members' own measures
        # meta-combined as the pooled block), keyed by the portfolio
        # PK + the snapshot date. ALWAYS the split write — NEVER
        # purged (the history_signal_performance lifecycle: past
        # snapshots are the point; a force rebuild re-measures and
        # upserts its own as-of date in place)
        if portfolio_perf:
            await copy_or_upsert_split_async(
                conn, PORTFOLIO_PERF_TABLE,
                [{"code": code, "sec_type": st,
                  "portfolio_type": ptype,
                  "date": m["date"],
                  # the COMBINED STATS of the individual in-portfolio
                  # signals (the cell-level redesign's new audit); dd =
                  # the orchestrating fragments' lenses combined. Both
                  # NULL where nothing measured
                  "pooled": (
                      json.dumps(p) if (p := _combine_member_stats(
                          [perf_by_id[i]
                           for i in groups.get(code, [])
                           if i in perf_by_id])) is not None
                      else None),
                  "benefit": json.dumps(m["benefit"]),
                  "gate": json.dumps(m["gate"]),
                  "dd": (
                      json.dumps(d) if (d := _combine_dd(
                          m["dd_fragments"])) is not None
                      else None)}
                 for code, m in sorted(portfolio_perf.items())],
                key_columns=PORTFOLIO_PERF_PK, date_column="date",
            )

        # portfolio_history_signals: the PORTFOLIO tier's EVENT history
        # (2026-10-21) — every composite/pair-native history row of the
        # run IS one portfolio event (the family replays only the pair
        # codes; the components' own history stays dropped); the
        # engine's ``_portfolio_event_members`` stamps — the sourced
        # component CELL EVENTS that triggered each event, each with
        # its own date — peel into the member_signals JSONB (the
        # association the UI's expandable rows render; untagged rows =
        # the pair-native subs, empty members). Same lifecycle as
        # history_signals: force purges the (sec_type, portfolio_type)
        # tier then conflict-free COPYs; incremental split-copies past
        # max(date) and upserts the rest (idempotent re-emission).
        hist_events = []
        for h in history_rows:
            members = h.pop("_portfolio_event_members", None)
            hist_events.append({
                "code": h["code"], "sec_type": h["sec_type"],
                "portfolio_type": ptype,
                "signal_type": h["signal_type"],
                "signal_sub_type": h["signal_sub_type"],
                "action": h["action"],
                "date": h["date"], "time": h["time"],
                "signal": h["signal"],
                "signal_reference": h["signal_reference"],
                "confidence": h["confidence"],
                "regime_state": h["regime_state"],
                "is_typical": h["is_typical"],
                "member_signals": json.dumps(members or []),
            })
        if force:
            await chunked_purge_async(
                conn, PORTFOLIO_HISTORY_TABLE,
                where_sql="sec_type = $1 AND portfolio_type = $2",
                params=(st, ptype), key_column="code",
            )
            if hist_events:
                await copy_insert_async(conn, PORTFOLIO_HISTORY_TABLE,
                                        hist_events)
        elif hist_events:
            await copy_or_upsert_split_async(
                conn, PORTFOLIO_HISTORY_TABLE, hist_events,
                key_columns=PORTFOLIO_HISTORY_PK, date_column="date",
            )

    # history_signals: force purges the scope then conflict-free COPY;
    # incremental split-copies past max(date) and upserts the rest.
    # The purge is CODE-SCOPED per the entry's history_purge_scopes
    # (default = the rule wipe scopes): concurrent writers of the same
    # signal_type at other codes never collide, and the hedge_pairs
    # family's three rule types (hp_rsi + hedge_pairs at the pair
    # codes, mov_rsi at the component codes) purge together
    if force:
        for purge_type, purge_codes in entry0.history_purge_scopes():
            await chunked_purge_async(
                conn, HISTORY_TABLE,
                where_sql="sec_type = $1 AND signal_type = $2 "
                          "AND code = ANY($3)",
                params=(st, purge_type, sorted(purge_codes)),
                key_column="code",
            )
        if history_rows:
            await copy_insert_async(conn, HISTORY_TABLE, history_rows)
    elif history_rows:
        await copy_or_upsert_split_async(
            conn, HISTORY_TABLE, history_rows, key_columns=HISTORY_PK,
            date_column="date",
        )

    # history_signal_performance: the run's MEASURED snapshot per rule
    # cell (every emitted row — trading since the 2026-10-19
    # starvation retreat, plus the ungated twins), keyed by the cell
    # PK + the snapshot
    # date (the prepared frame's data horizon) — the cell's OWN
    # measure block + the is_in_portfolio membership flag (slimmed
    # 2026-10-20: the combined tiers never ride the cell snapshot).
    # ALWAYS the split write — NEVER purged, not even under --force:
    # past snapshots are the point of the table (a cell's evidence
    # evolution, graduation and death survive the rules tier's
    # per-run wipes); a force rebuild re-measures and upserts its own
    # as-of date in place, and a same-day re-run upserts idempotently.
    perf_rows = [
        {"code": row["code"], "sec_type": row["sec_type"],
         "signal_type": row["signal_type"],
         "signal_sub_type": row["signal_sub_type"],
         "trend": row["trend"], "regime_state": row["regime_state"],
         "action": row["action"],
         # the cell's OWN measure only — the engine's extra payload
         # keys (the hedge dd lens, consumed by the portfolio audit
         # above through the fragments) never reach the cell snapshot
         "date": perf["date"],
         "n_episodes": perf["n_episodes"],
         "dir_ave_pct": perf["dir_ave_pct"],
         "dir_std_pct": perf["dir_std_pct"],
         "t_stat": perf["t_stat"],
         "hit_rate": perf["hit_rate"],
         "is_in_portfolio": row["is_in_portfolio"]}
        for row in rules_rows
        if (perf := perf_by_id.get(row["signal_rule_id"])) is not None
    ]
    if perf_rows:
        await copy_or_upsert_split_async(
            conn, PERF_TABLE, perf_rows, key_columns=PERF_PK,
            date_column="date",
        )
    logger.info(f"[{label}] {sec_type}: wrote {len(rules_rows)} rules, "
                f"{len(history_rows)} history rows, "
                f"{len(perf_rows)} performance snapshots"
                + (f", {len(portfolio_perf)} portfolio "
                   f"audit snapshots"
                   + (f", {len(hist_events)} portfolio history events"
                      if hist_events else "")
                   if ptype_label is not None else "")
                + f" ({'force' if force else 'incremental'})")
    return len(rules_rows), len(history_rows)


__all__ = ["run_family", "family_is_current", "RULES_TABLE",
           "HISTORY_TABLE", "PERF_TABLE", "PORTFOLIO_TABLE",
           "PORTFOLIO_PERF_TABLE", "PORTFOLIO_HISTORY_TABLE"]
