"""The signal rules pipeline (analysis.analysis_signals.run) —
orchestrates the families' RuleEntry engines over a sec_type's
universe and writes the tables of the redesigned tier:

  signal_rules    current-state: the family's rule scope for the
                  sec_type is DELETED then freshly inserted (cells that
                  stopped passing the gate must not linger); every row
                  carries a fresh surrogate signal_rule_id from
                  signal_rule_id_seq (identifies the row EMISSION — the
                  scope churns every run); rule_params carry the
                  CONSTANT rule only (the 2026-10-19 split); the wipe
                  scopes are (signal_type, codes, tier) triples — the
                  standalone families own the FALSE tier, a portfolio
                  family's source layer owns the TRUE tier of the
                  vocabulary it stamps
  history_signal_performance  the cell's OWN MEASURED evidence as a
                  DATED daily snapshot (the measure block — n /
                  dir_ave / std / t / hit), peeled from the rows'
                  _performance payloads and upserted on the PK + date;
                  NEVER purged, not even under --force (past snapshots
                  are the point: a cell's evidence evolution,
                  graduation and death survive the rules tier's
                  per-run wipes)
  history_signals force: the scope is purged then COPY'd (conflict
                  free); incremental: copy-or-upsert split past the
                  table's max date (idempotent re-emission)
  trend_days      the prepared frame's per-(code, date) TREND
                  classification into analysis.trend_days (the
                  consolidated analysis.trends component's store,
                  family-agnostic; written by the first family run
                  whose data outdates the store)

The PORTFOLIO families (engines.signal_families.FAMILY_PORTFOLIO_TYPE
— hedge_pairs) additionally write, in the same run, from the
orchestrating rows' peeled fragments:

  portfolio_signal_rules  ONE ROW PER PORTFOLIO RULE (code,
                  portfolio_type, portfolio_sub_type — the 2026-10-25
                  split: trio wash and spread are SEPARATE rows)
                  — wiped + rewritten with the rules; signal_rule_ids
                  = the rule's own orchestrating ids PLUS the MAPPED
                  REGISTERED CELLS (real signal_rules rows riding the
                  is_in_portfolio TRUE tier — never portfolio rows
                  themselves; the market cells ride every pair's
                  trio_wash rule only, a leg cell its own pair's rules
                  that witness the legs),
                  is_valid = the audited benefit gate AND at least one
                  orchestrating cell's own verdict
  history_portfolio_signal_performance  the dated PORTFOLIO AUDIT per
                  portfolio rule — the benefit payload (the hedge type's
                  max-drawdown-reduction claim), the gate digest with
                  the member benefit-boost scale, the coordinated-event
                  dd lens, and ``pooled`` = the COMBINED STATS of the
                  individual in-portfolio signals (their own measures
                  meta-combined); upsert-only, NEVER purged
  portfolio_history_signals  the PORTFOLIO EVENT tier — one row per
                  orchestrating event with THE EVENT DATE and the
                  member association (member_signals + the
                  cell_signals linkage into the source layer's
                  history rows), stamped with the event's
                  portfolio_sub_type; force purges the (sec_type,
                  portfolio_type) scope, incremental upserts past max
                  date

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
from _common.labels import PORTFOLIO_TYPE_CODE, SEC_TYPE_CODE, SIGNAL_TYPE_CODE

from analysis.analysis_signals.audit import (
    audit_family_rows,
    audit_orphan_rows,
    audit_portfolio_groups,
)
from analysis.analysis_signals.engines.signal_families import (
    FAMILY_PORTFOLIO_TYPE,
    SIGNAL_FAMILY_ENTRIES,
)

logger = logging.getLogger(__name__)

RULES_TABLE = "analysis_signals.signal_rules"
HISTORY_TABLE = "analysis_signals.history_signals"
TREND_TABLE = "analysis.trend_days"
PERF_TABLE = "analysis_signals.history_signal_performance"
PORTFOLIO_TABLE = "analysis_signals.portfolio_signal_rules"
PORTFOLIO_PERF_TABLE = \
    "analysis_signals.history_portfolio_signal_performance"
PORTFOLIO_HISTORY_TABLE = "analysis_signals.portfolio_history_signals"
RULE_ID_SEQ = "analysis_signals.signal_rule_id_seq"
RULES_PK = ["code", "sec_type", "signal_type", "signal_sub_type",
            "trend", "regime_state", "action", "is_in_portfolio"]
HISTORY_PK = ["code", "sec_type", "signal_type", "signal_sub_type",
              "date", "time", "is_in_portfolio"]
PERF_PK = ["code", "sec_type", "signal_type", "signal_sub_type",
           "trend", "regime_state", "action", "date"]
PORTFOLIO_PERF_PK = ["code", "portfolio_type", "portfolio_sub_type", "date"]
PORTFOLIO_HISTORY_PK = ["code", "sec_type", "portfolio_type",
                        "portfolio_sub_type", "signal_type",
                        "signal_sub_type", "date", "time"]

# the tier partition of a wipe/purge scope (the is_in_portfolio PK
# axis): True → the portfolio source tier, False → the standalone
# tier, None → both (the composite scopes that own their codes)
_TIER_SQL = {True: " AND is_in_portfolio",
             False: " AND NOT is_in_portfolio",
             None: ""}


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
    if label == "macd":
        # the family's value source: the builds' PRECOMPUTED typical
        # EMAs (ema12/ema26 — the fetch reads them, never recomputes)
        return ("SELECT code, max(date) AS latest FROM "
                f"stats.{sec_type}_tech_stats "
                "WHERE code = ANY($1) GROUP BY code", ())
    if label == "hedge_pairs":
        # the trio's value sources: the legs' composite closes, the
        # market's closes, the offsets estimates — ANY source growth
        # forces the rebuild (the universe itself is data-driven). One
        # synthetic row; the codes parameter rides as a passthrough
        # column (the shared call signature passes it positionally)
        return ("SELECT 'pairs' AS code, greatest("
                "(SELECT max(date) FROM stats.industry_basic_stats "
                " WHERE pool_size = 3),"
                "(SELECT max(date) FROM stats.index_basic_stats "
                " WHERE code = '000300' AND close IS NOT NULL),"
                "(SELECT max(start_date) FROM "
                " analysis_composites.industry_corr_benchmark_offsets"
                " WHERE pool_size = 3)"
                ") AS latest, $1::text[] AS scope",
                ())
    # bollinger: values are computed from the base OHLCV closes (the z
    # layer is in-engine)
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
    entry0 = entries[0](conn, sec_type, codes, boost=boost)
    frame = await entry0.prepare_frame()
    rules_rows: list[dict] = []
    history_rows: list[dict] = []
    if frame.empty:
        logger.warning(f"[{label}] {sec_type}: empty input frame — nothing "
                       f"to do")
    else:
        for i, entry_cls in enumerate(entries):
            # the FIRST entry runs on entry0 itself — entry0 is the
            # instance that fetched the frame, and a family may stash
            # fetch-time side state its run() needs (the hedge family's
            # component frame); the later entries are stateless
            entry = entry0 if i == 0 \
                else entry_cls(conn, sec_type, codes, boost=boost)
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
        if entry0.WRITES_TREND_DAYS:
            n_trend = await _write_trend_days(conn, sec_type, frame,
                                              force)
            if n_trend:
                logger.info(f"[{label}] {sec_type}: wrote {n_trend} "
                            f"trend days")

    # signal_rules: current-state — wipe THE RUN'S CODE SCOPES of the
    # family's signal_type(s), insert fresh. CODE-SCOPED and
    # TIER-PARTITIONED (concurrent writers of the SAME signal_type at
    # other codes — or the other tier at the same codes — never
    # collide); the family declares its scopes via rule_wipe_scopes as
    # (signal_type, codes, tier) triples
    for wipe_type, wipe_codes, tier in entry0.rule_wipe_scopes():
        await conn.execute(
            f"DELETE FROM {RULES_TABLE} "
            f"WHERE sec_type = $1 AND signal_type = $2 "
            f"AND code = ANY($3){_TIER_SQL[tier]}",
            st, wipe_type, sorted(wipe_codes),
        )
    perf_by_id: dict[int, dict] = {}
    portfolio_rule_by_id: dict[int, dict] = {}
    portfolio_perf_by_id: dict[int, dict] = {}
    portfolio_sub_by_id: dict[int, int] = {}
    sourced_member_by_id: dict[int, dict] = {}
    if rules_rows:
        # the surrogate rule ids (signal_rule_id): ONE batched nextval
        # draw stamped on the rows before COPY — the id identifies this
        # run's ROW EMISSION (the scope is wiped and re-inserted every
        # run). The _performance payloads peel the same way — the
        # cell's OWN measure block, the dated history_signal_
        # performance snapshot (the 2026-10-19 split) — and the
        # portfolio families' orchestrating rows additionally carry the
        # _portfolio_rule_params / _portfolio_performance fragments
        # (+ the row's _portfolio_sub_type marker, the 2026-10-25 sub
        # split's grouping key) peeled below into the portfolio tier
        # tables
        ids = await conn.fetch(
            f"SELECT nextval('{RULE_ID_SEQ}') AS id "
            f"FROM generate_series(1, $1::int)", len(rules_rows),
        )
        for row, rid in zip(rules_rows, ids):
            row["signal_rule_id"] = rid["id"]
            perf = row.pop("_performance", None)
            if perf is not None:
                perf_by_id[int(rid["id"])] = perf
            frag = row.pop("_portfolio_rule_params", None)
            if frag is not None:
                portfolio_rule_by_id[int(rid["id"])] = frag
            frag = row.pop("_portfolio_performance", None)
            if frag is not None:
                portfolio_perf_by_id[int(rid["id"])] = frag
            sub = row.pop("_portfolio_sub_type", None)
            if sub is not None:
                portfolio_sub_by_id[int(rid["id"])] = int(sub)
            marker = row.pop("_sourced_member", None)
            if marker is not None:
                sourced_member_by_id[int(rid["id"])] = marker
        for row in rules_rows:  # JSONB rides COPY as JSON text
            row["rule_params"] = json.dumps(row["rule_params"])
        await copy_insert_async(conn, RULES_TABLE, rules_rows)

    # history_signals: force purges the scope then conflict-free COPY;
    # incremental split-copies past max(date) and upserts the rest.
    # The purge is CODE-SCOPED + TIER-PARTITIONED per the entry's
    # history_purge_scopes (default = the rule wipe scopes); the
    # portfolio families' orchestrating events carry the
    # _portfolio_event_members payload peeled into
    # portfolio_history_signals before the COPY
    portfolio_events: list[dict] = []
    for row in history_rows:
        members = row.pop("_portfolio_event_members", None)
        if members is not None:
            portfolio_events.append({
                "code": row["code"], "sec_type": row["sec_type"],
                "signal_type": row["signal_type"],
                "signal_sub_type": row["signal_sub_type"],
                "portfolio_sub_type": members.get("portfolio_sub_type"),
                "action": row["action"], "date": row["date"],
                "time": row["time"], "signal": row["signal"],
                "signal_reference": row["signal_reference"],
                "confidence": row["confidence"],
                "regime_state": row["regime_state"],
                "is_typical": row["is_typical"],
                "member_signals": json.dumps(members["member_signals"]),
                "cell_signals": json.dumps(members["cell_signals"]),
            })
    if force:
        for purge_type, purge_codes, tier in entry0.history_purge_scopes():
            await chunked_purge_async(
                conn, HISTORY_TABLE,
                where_sql="sec_type = $1 AND signal_type = $2 "
                          "AND code = ANY($3)" + _TIER_SQL[tier],
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
    # PK + the snapshot date (the prepared frame's data horizon) — the
    # cell's OWN measure block. ALWAYS the split write — NEVER purged,
    # not even under --force: past snapshots are the point of the
    # table (a cell's evidence evolution, graduation and death survive
    # the rules tier's per-run wipes); a force rebuild re-measures and
    # upserts its own as-of date in place, and a same-day re-run
    # upserts idempotently.
    perf_rows = [
        {"code": row["code"], "sec_type": row["sec_type"],
         "signal_type": row["signal_type"],
         "signal_sub_type": row["signal_sub_type"],
         "trend": row["trend"], "regime_state": row["regime_state"],
         "action": row["action"],
         # the cell's OWN measure only
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
    n_port = 0
    port_groups: dict[tuple, dict] | None = None
    if label in FAMILY_PORTFOLIO_TYPE:
        n_port, port_groups = await _write_portfolio_tables(
            conn, sec_type, FAMILY_PORTFOLIO_TYPE[label], rules_rows,
            perf_by_id, portfolio_rule_by_id, portfolio_perf_by_id,
            portfolio_events, force, sourced_member_by_id,
            portfolio_sub_by_id,
        )
    # the post-write AUDIT GATE (2026-10-26, user-directed after the
    # portfolio cell-signals side-leak): re-derives the cross-tier
    # contracts — vocabulary decodability, rules<->history linkage,
    # performance linkage, the portfolio witnesses' side alignment /
    # ids reachability / coverage, the event member invariant, and the
    # orphan rows a universe shrink leaves behind — from what this run
    # just emitted and wrote. Violations are LOUD log lines; the run
    # never aborts on them (the log is the alarm — see audit.py's
    # docstring for the full incident-class inventory).
    n_bad = audit_family_rows(label, rules_rows, history_rows, perf_by_id)
    if port_groups is not None:
        n_bad += audit_portfolio_groups(label, port_groups, rules_rows,
                                        portfolio_events)
    n_bad += await audit_orphan_rows(conn, sec_type, label,
                                     entry0.rule_wipe_scopes())
    if n_bad:
        logger.warning(f"[audit] {label} {sec_type}: {n_bad} VIOLATION(S) "
                       f"— investigate the [audit] lines above")
    else:
        logger.info(f"[audit] {label} {sec_type}: all consistency checks "
                    f"passed")
    logger.info(f"[{label}] {sec_type}: wrote {len(rules_rows)} rules, "
                f"{len(history_rows)} history rows, "
                f"{len(perf_rows)} performance snapshots"
                f"{f', {n_port} portfolios' if n_port else ''}"
                f" ({'force' if force else 'incremental'})"
                )
    return len(rules_rows), len(history_rows)


def _combine_member_stats(members: list[dict]) -> dict | None:
    """The COMBINED STATS of the individual in-portfolio signals
    (history_portfolio_signal_performance.pooled): the member cells'
    own measured evidence — their _performance measure blocks —
    meta-combined into ONE population record: the member-cell count,
    the total episode count, the n-weighted mean dir_ave / hit, and
    the t-stat of the combined moments (per-cell n / dir_ave /
    dir_std pooled: total scatter = Σ n_i·(std_i² + (dir_i − dir̄)²)).
    Each member keeps its OWN unified dir_ave test — this block is the
    portfolio tier's combined RECORD, never a cell gate. None on
    portfolios whose members measured nothing."""
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


async def _write_portfolio_tables(
    conn, sec_type: str, portfolio_label: str, rules_rows: list[dict],
    perf_by_id: dict[int, dict], portfolio_rule_by_id: dict[int, dict],
    portfolio_perf_by_id: dict[int, dict], portfolio_events: list[dict],
    force: bool, sourced_member_by_id: dict[int, dict] | None = None,
    portfolio_sub_by_id: dict[int, int] | None = None,
) -> int:
    """The PORTFOLIO tier's three tables from the orchestrating rows'
    peeled fragments (ONE writer per portfolio family): the group rows
    (wiped + rewritten with the rules — ONE GROUP PER (code,
    portfolio_sub_type) since the 2026-10-25 sub split: trio wash and
    spread are SEPARATE portfolio rows), the dated benefit audit
    (upsert-only) and the event tier (force purges the (sec_type,
    portfolio_type) scope; incremental upserts past max date).
    ``portfolio_sub_by_id`` — the orchestrating rows' PORTFOLIO RULE
    markers (the engine's _portfolio_sub_type stamp; the grouping key).
    ``sourced_member_by_id`` — the REGISTERED STANDALONE cells' markers
    (the mov_rsi rows measured at the component codes with
    is_in_portfolio TRUE — real signal_rules rows, NEVER portfolio
    rows: each portfolio rule MAPS them through its signal_rule_ids,
    the marker's ``subs`` naming the target rules): the shared market
    cells ride every pair's trio_wash rule, a leg cell its own pair's
    rules that witness the legs (2026-10-25, user-directed "rsi
    signals ... by portfolio signal_rule_ids to map them"). Returns
    (the portfolio count written, the built groups — the post-write
    audit's input; None groups = nothing built)."""
    st = SEC_TYPE_CODE[sec_type]
    pt = PORTFOLIO_TYPE_CODE[portfolio_label]
    # one group per (portfolio code, portfolio_sub_type) — the 2026-10-25
    # sub split (user-directed "migrate trio wash and spread to as
    # portfolio_sub_type"): the pair's TWO portfolio rules are SEPARATE
    # rows, each with its own orchestrating ids, per-sub coordination
    # spec and audit. Per group: the orchestrating rows (the rule IS its
    # orchestrating cells), the construction fragment (identical across
    # its rows), the perf fragment, the writing entries, any cell verdict
    groups: dict[tuple[str, int], dict] = {}
    for row in rules_rows:
        rid = row["signal_rule_id"]
        frag = portfolio_rule_by_id.get(rid)
        if frag is None:
            continue  # not an orchestrating row of a portfolio family
        sub = portfolio_sub_by_id.get(int(rid))
        if sub is None:
            continue  # pre-split fragment (no portfolio sub marker)
        key = (row["code"], sub)
        g = groups.get(key)
        if g is None:
            g = groups.setdefault(key, {
                "sub": sub,
                "frag": frag,
                "ids": [],
                "perf": portfolio_perf_by_id.get(rid),
                "entries": [],
                "any_valid": False,
                "as_of": None,
            })
        else:
            # a second orchestrating row of the SAME portfolio rule (the
            # spread rule's hp_entry_spread + hp_exit_spread): merge ITS
            # subs spec into the group's fragment — the top-level
            # construction is pair-level and identical, the subs dicts
            # are per-sub and disjoint
            g["frag"] = {**g["frag"], "subs": {
                **g["frag"].get("subs", {}), **(frag.get("subs") or {})}}
        g["ids"].append(int(rid))
        if row["rule_entry"] not in g["entries"]:
            g["entries"].append(row["rule_entry"])
        g["any_valid"] = g["any_valid"] or bool(row["is_valid"])
        perf = perf_by_id.get(rid)
        if perf is not None and g["as_of"] is None:
            g["as_of"] = perf["date"]
    if not groups:
        # no orchestrating cell survived its gate this run — the group
        # tier still wipes (cells that stopped passing must not linger)
        await conn.execute(
            f"DELETE FROM {PORTFOLIO_TABLE} "
            f"WHERE sec_type = $1 AND portfolio_type = $2", st, pt)
        return 0, None

    # the REGISTERED STANDALONE cells join the portfolio rules'
    # signal_rule_ids (2026-10-25, user-directed "rsi signals themselves
    # should not sit in portfolio signals — by portfolio signal_rule_ids
    # to map them"): they are real signal_rules rows riding the
    # is_in_portfolio TRUE tier (a SEPARATE row from any standalone twin
    # — is_in_portfolio is a PK member there), never portfolio rows
    # themselves. The engine's marker names each row's TARGET RULES
    # (subs): the shared market cells ride every pair's trio_wash rule
    # only (the spread disregards the benchmark), a leg cell its own
    # pair's rules that witness the legs (the legs witness both)
    if sourced_member_by_id:
        for row in rules_rows:
            marker = sourced_member_by_id.get(row["signal_rule_id"])
            if marker is None:
                continue
            rid = int(row["signal_rule_id"])
            subs = marker.get("subs") or ()
            if marker.get("market"):
                targets = [g for g in groups.values() if g["sub"] in subs]
            else:
                leg = marker.get("leg")
                targets = [
                    g for g in groups.values() if g["sub"] in subs
                    and leg in ((g["frag"].get("portfolio") or {}).get(
                        "legs") or [])
                ]
            for g in targets:
                g["ids"].append(rid)

    group_rows: list[dict] = []
    perf_rows: list[dict] = []
    for (code, sub), g in groups.items():
        gate = (g["perf"] or {}).get("gate") or {}
        is_valid = bool(gate.get("passed")) and g["any_valid"]
        params = {"axis": portfolio_label, **g["frag"]}
        group_rows.append({
            "code": code, "sec_type": st, "portfolio_type": pt,
            "portfolio_sub_type": sub,
            "signal_rule_ids": g["ids"],
            "is_valid": is_valid,
            "rule_entry": "|".join(g["entries"]),
            "rule_params": params,
        })
        perf_rows.append({
            "code": code, "sec_type": st, "portfolio_type": pt,
            "portfolio_sub_type": sub,
            "date": g["as_of"],
            # the COMBINED STATS of the rule's own cells (the
            # orchestrating population + the mapped registered cells)
            "pooled": _combine_member_stats(
                [perf_by_id.get(i) for i in g["ids"]]),
            "benefit": (g["perf"] or {}).get("benefit") or {},
            "gate": gate,
            "dd": (g["perf"] or {}).get("dd"),
        })
    if not group_rows:
        await conn.execute(
            f"DELETE FROM {PORTFOLIO_TABLE} "
            f"WHERE sec_type = $1 AND portfolio_type = $2", st, pt)
        return 0, groups
    await conn.execute(
        f"DELETE FROM {PORTFOLIO_TABLE} "
        f"WHERE sec_type = $1 AND portfolio_type = $2", st, pt)
    for row in group_rows:  # JSONB rides COPY as JSON text
        row["rule_params"] = json.dumps(row["rule_params"])
    await copy_insert_async(conn, PORTFOLIO_TABLE, group_rows)
    if perf_rows:
        for row in perf_rows:  # JSONB columns ride COPY as JSON text
            for col in ("pooled", "benefit", "gate", "dd"):
                if row[col] is not None:
                    row[col] = json.dumps(row[col])
        await copy_or_upsert_split_async(
            conn, PORTFOLIO_PERF_TABLE, perf_rows,
            key_columns=PORTFOLIO_PERF_PK, date_column="date",
        )
    if portfolio_events:
        for row in portfolio_events:
            row["portfolio_type"] = pt
        if force:
            await chunked_purge_async(
                conn, PORTFOLIO_HISTORY_TABLE,
                where_sql="sec_type = $1 AND portfolio_type = $2",
                params=(st, pt), key_column="code",
            )
            await copy_insert_async(conn, PORTFOLIO_HISTORY_TABLE,
                                    portfolio_events)
        else:
            await copy_or_upsert_split_async(
                conn, PORTFOLIO_HISTORY_TABLE, portfolio_events,
                key_columns=PORTFOLIO_HISTORY_PK, date_column="date",
            )
    logger.info(f"[portfolio] {portfolio_label} {sec_type}: "
                f"{len(group_rows)} portfolios, "
                f"{len(perf_rows)} audit snapshots, "
                f"{len(portfolio_events)} events")
    return len(group_rows), groups


__all__ = ["run_family", "family_is_current", "RULES_TABLE",
           "HISTORY_TABLE", "PERF_TABLE"]
