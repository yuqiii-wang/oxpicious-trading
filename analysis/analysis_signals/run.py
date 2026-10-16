"""The signal rules pipeline (analysis.analysis_signals.run) —
orchestrates the families' RuleEntry engines over a sec_type's
universe and writes the tables of the redesigned tier:

  signal_rules    current-state: the family's rule scope for the
                  sec_type is DELETED then freshly inserted (cells that
                  stopped passing the gate must not linger)
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
from _common.labels import SEC_TYPE_CODE, SIGNAL_TYPE_CODE

from analysis.analysis_signals.engines.signal_families import (
    SIGNAL_FAMILY_ENTRIES,
)

logger = logging.getLogger(__name__)

RULES_TABLE = "analysis_signals.signal_rules"
HISTORY_TABLE = "analysis_signals.history_signals"
TREND_TABLE = "analysis.trend_days"
RULES_PK = ["code", "sec_type", "signal_type", "signal_sub_type",
            "trend", "regime_state", "action"]
HISTORY_PK = ["code", "sec_type", "signal_type", "signal_sub_type",
              "is_ohlcv_boosted", "date", "time"]

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
    sig = SIGNAL_TYPE_CODE[label]
    if not codes:
        logger.info(f"[{label}] no codes for sec_type={sec_type} — skip")
        return 0, 0
    if not force and await family_is_current(conn, sec_type, label, codes):
        logger.info(f"[{label}] {sec_type}: all {len(codes)} codes at the "
                    f"data horizon with a live rules scope — skip")
        return 0, 0

    entries = SIGNAL_FAMILY_ENTRIES[label]
    frame = await entries[0](conn, sec_type, codes).prepare_frame()
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
                boost_note = (" · ohlcv-boost: " + ", ".join(parts)
                              + f" (+{boost_report.get('cells', 0)} "
                              f"cells stamped)")
            logger.info(f"[{label}] {entry.entry_name}: {len(r)} rule "
                        f"cells, {len(h)} history events{boost_note}")
        n_trend = await _write_trend_days(conn, sec_type, frame, force)
        if n_trend:
            logger.info(f"[{label}] {sec_type}: wrote {n_trend} trend days")

    # signal_rules: current-state — wipe the family's scope, insert fresh
    await conn.execute(
        f"DELETE FROM {RULES_TABLE} WHERE sec_type = $1 AND signal_type = $2",
        st, sig,
    )
    if rules_rows:
        for row in rules_rows:  # JSONB rides COPY as JSON text
            row["rule_params"] = json.dumps(row["rule_params"])
        await copy_insert_async(conn, RULES_TABLE, rules_rows)

    # history_signals: force purges the scope then conflict-free COPY;
    # incremental split-copies past max(date) and upserts the rest
    if force:
        await chunked_purge_async(
            conn, HISTORY_TABLE,
            where_sql="sec_type = $1 AND signal_type = $2",
            params=(st, sig), key_column="code",
        )
        if history_rows:
            await copy_insert_async(conn, HISTORY_TABLE, history_rows)
    elif history_rows:
        await copy_or_upsert_split_async(
            conn, HISTORY_TABLE, history_rows, key_columns=HISTORY_PK,
            date_column="date",
        )
    logger.info(f"[{label}] {sec_type}: wrote {len(rules_rows)} rules, "
                f"{len(history_rows)} history rows "
                f"({'force' if force else 'incremental'})")
    return len(rules_rows), len(history_rows)


__all__ = ["run_family", "family_is_current", "RULES_TABLE",
           "HISTORY_TABLE"]
