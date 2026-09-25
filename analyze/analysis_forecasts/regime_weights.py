"""Per-code self-adaptive market-regime WEIGHTS step
(analyze.analysis_forecasts.regime_weights).

Populates analysis_forecasts.regime_weights — the walk-forward
w(code, family, regime; stat_date) fitted ONLY on information realized
by the snapshot date (the Phase-A study's algorithm, §3 of
docs/market_regimes_study.md):

  fit window   the snapshot's K prior annual snapshots (Y-K, Y-1] —
               every snapshot s in the window has its own
               trailing-window forward stats fully realized long before
               year-end Y (a snapshot's mixed metrics reach s + 1 month
               + 20 trading days), so NO look-ahead. (The study's
               realized definition re-detected next-month days under
               frozen bars; this production fit uses each fit
               snapshot's OWN realized mixed metric — the same
               information, exactly computable from the emitted tables,
               documented as the display-tier deviation.)
  evidence     per (family, code, regime): the fit window's mixed rows
               (period='mixed', non-flat sides), sign-aligned
               dir_ave = ±ave_change (top/upper −, bottom/lower +)
               n-weighted by occurrence_count.
  weights      raw = Σ(occ·dir)/Σocc;  shrink = N/(N+10);
               what = GREATEST(raw,0)·shrink normalized per
               (family, code) (all-zero → uniform 1/4);
               w = 0.7·what + 0.075.

EVIDENCE / DISPLAY tier (the study's §4 verdict): score = confidence ×
w was tested head-to-head against the equal-confidence ordering and did
NOT win out-of-sample — signal_order stays on confidence; the weights
feed the forecasts UI (which regimes pay for each code) and future
research.

Incremental: a stat_date's weights never change once written (its fit
window is frozen), so only snapshots MISSING from the table are
computed — scoped to the caller's run scope via ``stat_dates`` (the
ROLLING LATEST key moves forward daily; an unscoped run would fit one
weight snapshot per day forever); --force recomputes all of the
sec_type's snapshots. Stale keys (retired rolling-latest dates) are
swept by the forecasts writer's stale-key sweep. Fit window on the
ANNUAL grid: the K prior year-end snapshots (a snapshot at year-end Y
has its forward windows realized by Feb Y+1, so the snapshot Y-1 and
older are always safe evidence at fit time Y; a mid-year ROLLING
LATEST key fits from the same prior years — no look-ahead).
"""
from __future__ import annotations

import json
import time
from datetime import date

import numpy as np
import pandas as pd

from _common.build_commons import rec_cols
from _common.db_commons import (
    copy_insert_async,
    _wait_for_replica_lag_async,
    DEFAULT_MAX_REPLICA_LAG_MB,
)
from _common.df_utils import host_array

from analyze.analysis_forecasts.config import (
    REGIME_WEIGHTS_TABLE,
    WEIGHT_FAMILIES,
    WEIGHT_K_SNAPSHOTS,
    WEIGHT_LAMBDA,
    WEIGHT_SHRINK_N,
)

import logging
logger = logging.getLogger(__name__)


def _year_end_shift(y: date, k: int) -> date:
    """The year-END k annual-snapshot steps before the snapshot ``y``
    (the annual grid's fit-window shift — snapshot dates are year-ends,
    so shifting by calendar months would skip them)."""
    return date(y.year - k, 12, 31)

# (family, motivation table, has_flat_side) — the evidence SELECTs'
# family-specific shapes. Every family table carries regime_state +
# side; margin_ratio_state additionally has 'flat'
# sides (no directional claim — excluded from the evidence).
_FAMILY_SOURCES: tuple[tuple[str, str, bool], ...] = (
    ("mov_rsi", "analysis_forecasts.mov_rsi", False),
    ("mov_std", "analysis_forecasts.mov_std", False),
    ("margin_ratio_state", "analysis_forecasts.margin_ratio_state", True),
    ("mov_pairs", "analysis_forecasts.mov_pairs", False),
    ("mov_pairs_ema", "analysis_forecasts.mov_pairs_ema", False),
    ("high_low_streaks", "analysis_forecasts.high_low_streaks", False),
    ("pe_state", "analysis_forecasts.pe_state", False),
    ("dividend_state", "analysis_forecasts.dividend_state", False),
)
assert {f for f, _, _ in _FAMILY_SOURCES} == set(WEIGHT_FAMILIES)

# One SELECT for ALL pending snapshots of a sec_type, then a COPY load:
# union the families' mixed-row evidence, expand every evidence row to the
# pending snapshots whose fit window covers it, aggregate per
# (stat_date, family, code, regime), shrink + normalize + blend, and
# project the final weight rows.
#
# 2026-09-25 batched single-pass + COPY (replaces the per-snapshot
# INSERT ... SELECT loop):
#   - the windows of snapshots K=5 apart overlap ~80%, so the loop re-read
#     the family tables and the delay-0 mixed slice once per snapshot; the
#     expansion join `i.stat_date > w.lo AND i.stat_date <= w.hi` against
#     the pending (m, lo, hi) list serves every snapshot in ONE pass;
#   - INSERT ... SELECT never parallelizes in PostgreSQL (38-51s per
#     snapshot observed in the 2026-09-25 one-code profile), while a plain
#     SELECT runs the same reads on parallel workers — so the aggregation
#     stays in SQL inside a plain SELECT and the tiny row output
#     (~71K rows for the whole index sec_type) is fetched and COPY-loaded
#     instead. Output identical to the loop (same sums per group; the
#     ROUND(..., 6) presentation absorbs float summation-order noise).
# The evidence read-out — RAW columns only, no calculation, no rounding
# (the repo's SQL contract: SQL reads, Python computes). One pass over the
# families' mixed-row evidence for the sec_type, bounded to the union of
# the pending snapshots' fit windows ($2 = min lo, $3 = max hi — the
# windows of consecutive year-end snapshots tile contiguously, so the
# union is one range). The per-snapshot assignment happens in Python
# (each identity year Y feeds snapshots m with m.year ∈ [Y+1, Y+K]).
#
# 2026-09-25 evolution: per-snapshot INSERT loop → batched SQL aggregate
# → THIS raw read-out + Python fit. The aggregation / shrink / normalize /
# blend / ROUND stay out of SQL entirely; the read is a parallel plain
# SELECT (INSERT ... SELECT never parallelizes in PostgreSQL).
_EVIDENCE_SQL = """
WITH fam AS (
    {family_selects}
)
SELECT i.stat_date,
       f.family,
       f.code,
       f.regime_state,
       f.side,
       r.ave_change::float8 AS ave_change,
       r.occurrence_count::float8 AS occurrence_count
FROM fam f
JOIN analysis_forecasts.forecast_identities i
     ON i.forecast_id = f.forecast_id
JOIN analysis_forecasts.forecast_results r
     ON r.forecast_id = f.forecast_id AND r.period = 'mixed'
    -- the delay-0 (fresh-signal) blended row — one per forecast_id
     AND r.delay = 0
WHERE i.sec_type = $1::text
  AND i.stat_date > $2::date AND i.stat_date <= $3::date
"""

# The regime_weights write columns (the SELECT's projection order).
_WEIGHTS_COLUMNS = ("stat_date", "sec_type", "code", "family", "regime",
                    "weight", "evidence", "k_snapshots", "shrink_n",
                    "lambda")

# COPY chunk ceiling = the shared commit-chunk target (100K rows): the
# rows are sorted by code (the partition key) so every chunk commits
# code-aligned and the replica slot advances between chunks.
_COPY_CHUNK_ROWS = 100_000

_MISSING_MONTHS_SQL = """
    SELECT DISTINCT i.stat_date
    FROM analysis_forecasts.forecast_identities i
    WHERE i.sec_type = $1::text
      AND i.bucket = ANY($2::text[])
      {scope_clause}
      AND NOT EXISTS (
          SELECT 1 FROM analysis_forecasts.regime_weights w
          WHERE w.sec_type = i.sec_type
            AND w.stat_date = i.stat_date)
    ORDER BY i.stat_date
"""


def _family_select(family: str, table: str, has_flat: bool) -> str:
    flat_filter = "  AND m.side <> 'flat'" if has_flat else ""
    return f"""
    SELECT '{family}'::text AS family,
           m.forecast_id,
           m.code,
           m.regime_state,
           m.side
    FROM {table} m
    WHERE m.regime_state IS NOT NULL{flat_filter}
    """


def _build_evidence_sql() -> str:
    selects = "\n    UNION ALL\n".join(
        _family_select(f, t, flat) for f, t, flat in _FAMILY_SOURCES)
    return _EVIDENCE_SQL.format(family_selects=selects)


def _weight_rows_from_evidence(
    evidence, months: list[date], sec_type: str,
) -> list[dict]:
    """The vectorized fit over the RAW evidence frame — the Phase-C
    algorithm, all math and rounding in Python (SQL never calculates):

      - expand each identity year Y to the pending snapshots it feeds
        (m.year ∈ [Y+1, Y+K] — the fit-window arithmetic of
        _year_end_shift on the annual grid);
      - raw = Σ(occ·dir) / Σocc per (stat_date, family, code, regime),
        dir sign-aligned (top/upper −, else +; NULL rows skip the sums
        exactly like SQL's SUM);
      - pos = max(raw, 0) · n/(n + SHRINK_N) — SQL's GREATEST(NULL, 0)
        is 0, so a NULL raw (Σocc = 0) contributes pos 0;
      - tot = Σ pos per (stat_date, family, code);
      - weight = ROUND(LAMBDA · (pos/tot if tot > 0 else uniform)
        + uniform, 6); evidence = {raw, n, pos, tot} at 6 dp.
    """
    ev = evidence.rename(columns={"regime_state": "regime"})
    ev["dir_ave"] = (
        np.where(ev["side"].isin(("top", "upper")), -1.0, 1.0)
        * ev["ave_change"]
    )
    ev["occ_dir"] = ev["occurrence_count"] * ev["dir_ave"]
    ev["year"] = pd.to_datetime(ev["stat_date"]).dt.year

    # snapshot expansion — one vectorized slice per pending snapshot; the
    # stat_date column lands as ONE numpy datetime64[D] repeat (a python
    # date setitem per slice trips a cudf.pandas fallback)
    parts = []
    lengths = []
    for m in months:
        sel = ev.loc[(ev["year"] >= m.year - WEIGHT_K_SNAPSHOTS)
                     & (ev["year"] <= m.year - 1),
                     ["family", "code", "regime", "occ_dir",
                      "occurrence_count"]].copy()
        lengths.append(len(sel))
        parts.append(sel)
    fit = parts[0] if len(parts) == 1 else pd.concat(parts,
                                                     ignore_index=True)
    fit["stat_date"] = np.repeat(
        np.array([m for m in months], dtype="datetime64[D]"), lengths)

    grp = fit.groupby(["stat_date", "family", "code", "regime"],
                      sort=False).agg(
        occ_dir=("occ_dir", "sum"),
        n=("occurrence_count", "sum"),
    ).reset_index()

    raw = grp["occ_dir"] / grp["n"].where(grp["n"] != 0)
    n_arr = grp["n"].to_numpy()
    pos = np.where(np.isnan(raw.to_numpy()), 0.0,
                   np.maximum(raw.to_numpy(), 0.0)
                   * n_arr / (n_arr + WEIGHT_SHRINK_N))
    grp["pos"] = pos
    tot = grp.groupby(["stat_date", "family", "code"],
                      sort=False)["pos"].transform("sum")

    uniform = round((1.0 - WEIGHT_LAMBDA) / 4, 6)
    tot_arr = tot.to_numpy()
    # zero-tot rows take the uniform branch; the denominator guard keeps
    # the division defined without ndarray's unsupported `where` kwarg
    ratio = pos / np.where(tot_arr > 0, tot_arr, 1.0)
    w_raw = np.where(tot_arr > 0, ratio, uniform)
    weight = np.round(w_raw * WEIGHT_LAMBDA + uniform, 6)

    # the evidence JSONB diagnostics at fixed 6 dp — the same text SQL's
    # ROUND(numeric, 6) stored (jsonb normalizes key order server-side);
    # one wholesale host conversion, then C-speed string joins. (A Σocc=0
    # group would store raw null in SQL — none exist; the 0.0 fill only
    # feeds pos, which matches SQL's GREATEST(NULL, 0) = 0.)
    codes = grp["code"].to_numpy()
    fams = grp["family"].to_numpy()
    regs = grp["regime"].to_numpy()
    # the group keys ride the datetime64 stat_date column — host_array
    # detaches the cudf proxy ndarray first, then pure-numpy conversion
    # to python DATEs (the proxied astype/tolist would trip fallbacks)
    stat_vals = (host_array(grp["stat_date"].to_numpy())
                 .astype("datetime64[D]").astype(object).tolist())
    raw_arr = raw.to_numpy()
    ev_json = [
        '{"n": ' + str(int(n))
        + ', "pos": ' + f"{float(p):.6f}"
        + ', "raw": ' + f"{0.0 if r != r else float(r):.6f}"
        + ', "tot": ' + f"{float(t):.6f}" + '}'
        for r, n, p, t in zip(raw_arr, n_arr, pos, tot_arr)
    ]
    return [
        {"stat_date": stat_date, "sec_type": sec_type, "code": code,
         "family": family, "regime": regime, "weight": float(w),
         "evidence": e, "k_snapshots": WEIGHT_K_SNAPSHOTS,
         "shrink_n": WEIGHT_SHRINK_N, "lambda": WEIGHT_LAMBDA}
        for stat_date, code, family, regime, w, e in zip(
            stat_vals, codes, fams, regs, weight.tolist(), ev_json)
    ]


async def run_regime_weights(conn, sec_type: str, *, force: bool = False,
                             stat_dates: list[date] | None = None,
                             ) -> int:
    """Compute the sec_type's MISSING regime-weight snapshots (all of
    them under --force): one raw evidence read-out, the vectorized
    Python fit, COPY-load. Returns rows written.

    ``stat_dates`` scopes the targets to the caller's snapshot scope
    (the run's compute list) — without it every identity stat_date
    missing from the table is targeted. The scope matters under the
    ROLLING LATEST key: a fresh key appears every data day, so an
    unscoped run would fit one weight snapshot per day forever. (A
    snapshot's weights never change once written — its fit window is
    the FROZEN prior years' evidence — so first-write is final.)"""
    t0 = time.time()
    logger.info(f"  [{sec_type}] Regime weights "
                f"(K={WEIGHT_K_SNAPSHOTS}, shrink={WEIGHT_SHRINK_N:g}, "
                f"λ={WEIGHT_LAMBDA:g})...")
    if force:
        await conn.execute(
            f"DELETE FROM {REGIME_WEIGHTS_TABLE} WHERE sec_type = $1",
            sec_type,
        )

    scope_clause = ("AND i.stat_date = ANY($3::date[])"
                    if stat_dates is not None and not force else "")
    months = [r["stat_date"] for r in await conn.fetch(
        _MISSING_MONTHS_SQL.format(scope_clause=scope_clause),
        sec_type, list(WEIGHT_FAMILIES),
        *( (stat_dates,) if scope_clause else () ),
    )]
    if not months:
        logger.info(f"  [{sec_type}]   up to date; nothing to compute.")
        return 0
    logger.info(f"  [{sec_type}]   {len(months)} snapshots to compute "
                f"({months[0]} .. {months[-1]})")

    # the read-out spans the pending windows' union range — the windows
    # of consecutive year-end snapshots tile contiguously, so one
    # (min lo, max hi] bound covers every snapshot's evidence
    lo = _year_end_shift(months[0], WEIGHT_K_SNAPSHOTS + 1)
    hi = _year_end_shift(months[-1], 1)
    # The bare 3-way hash join is parallel-eligible but the planner
    # serial-prices it once the SQL-side aggregation is gone (24.8s
    # parallel vs 49.3s serial, measured 2026-09-25) — the zeroed
    # parallel costing GUCs live ONLY inside this read-out transaction.
    async with conn.transaction():
        await conn.execute("SET LOCAL parallel_setup_cost = 0")
        await conn.execute("SET LOCAL parallel_tuple_cost = 0")
        rows = await conn.fetch(_build_evidence_sql(), sec_type, lo, hi)
    evidence = pd.DataFrame(
        rec_cols(rows),
        columns=["stat_date", "family", "code", "regime_state", "side",
                 "ave_change", "occurrence_count"],
    )
    weight_rows = _weight_rows_from_evidence(evidence, months, sec_type)
    weight_rows.sort(key=lambda r: (r["code"], r["stat_date"],
                                    r["family"], r["regime"]))
    n_total = 0
    for i in range(0, len(weight_rows), _COPY_CHUNK_ROWS):
        # Between commit chunks (rows are code-sorted, so chunks are
        # code-aligned — the partition-key chunking rule): wait out
        # standby replay lag before generating more WAL.
        await _wait_for_replica_lag_async(
            conn, DEFAULT_MAX_REPLICA_LAG_MB)
        n_total += await copy_insert_async(
            conn, REGIME_WEIGHTS_TABLE, weight_rows[i:i + _COPY_CHUNK_ROWS],
            list(_WEIGHTS_COLUMNS))
    logger.info(f"  [{sec_type}]   wrote {n_total:,} weight rows "
                f"({time.time() - t0:.1f}s)")
    return n_total
