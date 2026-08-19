"""Orchestrator for sec_alloc_perf_attribution computation.

``build_and_insert`` orchestrates steps 0-9 (defined in the sibling
modules) and is the single public function in this package.
"""
from __future__ import annotations

import datetime
from typing import Optional, Set

import pandas as pd

from _common.build_commons import copy_or_upsert_split_async
from _common.df_utils import maybe_enable_cudf_pandas

from analyze.sec_alloc_perf_attribution.config import (
    CORR_WINDOWS,
    TABLE,
)

from ._lookback import filter_dataframes_for_lookback, LOOKBACK_TRADING_DAYS
from ._pivots import prepare_pivots
from ._filters import filter_target_dates, filter_to_target_rows
from ._merge import merge_subject_with_benchmarks, attach_shared_weights
from ._gpu_corr import compute_rolling_correlations_bulk
from ._etf import attach_etf_amounts, compute_ma5_ratio
from ._sanitize import select_and_sanitize


# ---------------------------------------------------------------------------
#  Orchestrator
# ---------------------------------------------------------------------------
async def build_and_insert(conn, subject_closes: pd.DataFrame,
                           index_closes: pd.DataFrame,
                           shared_weights: dict,
                           etf_amount_by_index: pd.DataFrame,
                           sec_type: str,
                           *,
                           target_dates: Optional[Set[datetime.date]] = None,
                           ) -> int:
    """For each subject code, cross-join with all indices on date, insert.

    Processes one subject at a time to bound memory. Rolling
    correlations are pre-computed in bulk (GPU path) for ALL subjects
    before the per-subject loop, which enables GPU breakeven (~53x
    speedup on RTX 5090 for the corr computation).

    See module docstring for the step decomposition.

    Args:
        conn: asyncpg connection.
        subject_closes: DataFrame [date, code, subject_close].
        index_closes:   DataFrame [date, benchmark_code, benchmark_close].
        shared_weights: dict {(subject_code, benchmark_code):
                        (code_wt, bench_wt)}.
        etf_amount_by_index: DataFrame [date, index_code, etf_amount].
        sec_type: 'index' (self-pairs excluded) or 'etf'.
        target_dates: when non-empty, only rows whose date is in this set
            are upserted (incremental mode). The dataframes are pre-filtered
            to the lookback window (``LOOKBACK_TRADING_DAYS`` trading days
            before the earliest target date), so rolling correlations and
            the MA5 ratio are computed only on the recent history needed
            for the target dates. When None, all rows are upserted
            (full recompute over the full history).

    Returns:
        Total rows inserted.
    """
    n_subjects = subject_closes["code"].nunique() if not subject_closes.empty else 0
    n_indices = (index_closes["benchmark_code"].nunique()
                 if not index_closes.empty else 0)
    print(f"    -> {n_subjects} {sec_type}s x {n_indices} indices "
          f"(cross-product on shared dates)", flush=True)

    if n_subjects == 0 or n_indices == 0:
        print("    -> no data to insert.", flush=True)
        return 0

    # Step 2: DB-side skip for already-present target dates.
    target_dates = await filter_target_dates(conn, target_dates)
    if target_dates is not None and len(target_dates) == 0:
        print("    -> all target dates already present; nothing to do.",
              flush=True)
        return 0

    # Step 0: incremental-mode lookback pre-filter.
    # When target_dates is specified, trims all three dataframes to
    # only dates within the lookback window. This reduces rolling-corr
    # compute volume by ~10-15x for single-date rebuilds.
    subject_closes, index_closes, etf_amount_by_index = (
        filter_dataframes_for_lookback(
            subject_closes, index_closes, etf_amount_by_index,
            target_dates if target_dates is not None else set(),
        )
    )

    # Pre-compute: for each subject, which benchmarks have non-zero
    # composition overlap?  This lets us skip expensive rolling-corr
    # computation for the ~90%+ of pairs where indices are disjoint.
    broad_market_codes: set[str] = set()
    _bm_codes = index_closes["benchmark_code"].unique()
    broad_codes_rows = await conn.fetch("""
        SELECT DISTINCT code FROM stats.sec_classification
        WHERE sector_id = 'BROAD' AND type = 'index' AND is_active = TRUE
    """)
    broad_market_codes = {r["code"] for r in broad_codes_rows} & set(_bm_codes)

    # Build: subject_code -> set(benchmark_code) with non-zero shared
    # weight (composition overlap). Zero-weight pairs are NOT stored
    # here — they are handled by the live pipeline which sets them to
    # zero via LEFT JOIN + COALESCE on analysis.sec_alloc_perf_attribution.
    subject_related_benchmarks: dict[str, set[str]] = {}
    for (sc, bc), (cw, _bw) in shared_weights.items():
        if cw and cw > 0:
            subject_related_benchmarks.setdefault(sc, set()).add(bc)
    for sc in subject_related_benchmarks:
        subject_related_benchmarks[sc] |= broad_market_codes

    # Step 1: pre-pivot ALL benchmarks (needed for rolling-corr wide frame).
    benchmark_close_wide, _etf_amount_wide, etf_amount_long = (
        prepare_pivots(index_closes, etf_amount_by_index)
    )

    total = 0
    subject_codes = sorted(subject_closes["code"].unique())

    # ---- Bulk GPU rolling correlation pre-computation ------------------
    # Pre-compute ALL rolling correlations for ALL subjects against ALL
    # their related benchmarks in one batch. This enables GPU utilization
    # for the entire corr computation, giving ~53x speedup on RTX 5090.
    print("    -> Bulk GPU rolling correlation pre-computation...", flush=True)
    corr_bulk = compute_rolling_correlations_bulk(
        subject_closes, benchmark_close_wide,
        subject_related_benchmarks, subject_codes,
    )
    # Build a lookup keyed on (date, code, benchmark_code) for fast joins.
    if not corr_bulk.empty:
        corr_bulk = corr_bulk.set_index(["date", "code", "benchmark_code"])

    # ---- Per-subject pipeline (shared weights, ETF, MA5, upsert) ------
    for i, subject_code in enumerate(subject_codes):
        related = subject_related_benchmarks.get(subject_code, set())
        if not related:
            if (i + 1) % 10 == 0 or (i + 1) == n_subjects:
                print(f"    [{i + 1}/{n_subjects}] {subject_code}: "
                      f"skip (no non-zero shared weight with any benchmark)",
                      flush=True)
            continue

        # Filter index_closes to ONLY benchmarks with non-zero overlap
        related_idx = index_closes[
            index_closes["benchmark_code"].isin(related)
        ].copy()

        # Step 3: inner-join subject with related benchmarks on date.
        one_subject = subject_closes[subject_closes["code"] == subject_code]
        merged = merge_subject_with_benchmarks(
            one_subject, related_idx, sec_type
        )
        if merged is None:
            continue

        # Step 4: attach shared weights (vectorized lookup).
        merged = attach_shared_weights(merged, shared_weights, subject_code)

        # Step 5 (bulk): lookup pre-computed rolling correlations.
        if not corr_bulk.empty:
            merged = merged.join(
                corr_bulk,
                on=["date", "code", "benchmark_code"],
                how="left",
            )

        # Step 6: attach ETF turnover (benchmark + subject for index).
        merged = attach_etf_amounts(
            merged, etf_amount_long, sec_type, subject_code
        )

        # Step 7: capped ratio + grouped rolling MA(5).
        merged = compute_ma5_ratio(merged)

        # Step 8: incremental-mode row filter.
        merged = filter_to_target_rows(
            merged, target_dates, subject_code, i, n_subjects
        )

        # Step 9: select output cols + sanitize + upsert.
        rows = select_and_sanitize(merged)
        if not rows:
            continue

        n_copied, n_upserted = await copy_or_upsert_split_async(
            conn, TABLE, rows,
            key_columns=["code", "date", "sec_type", "benchmark_code"],
        )
        n = n_copied + n_upserted
        total += n
        via = "COPY" if n_copied > 0 and n_upserted == 0 else \
              f"COPY+upsert ({n_copied}+{n_upserted})" if n_copied > 0 else \
              "upsert"
        if (i + 1) % 10 == 0 or (i + 1) == n_subjects:
            print(f"    [{i + 1}/{n_subjects}] {subject_code}: {len(rows):,} rows "
                  f"(cumulative: {total:,})", flush=True)

    return total
