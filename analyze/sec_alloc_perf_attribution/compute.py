"""Pure-pandas + async upsert transformation logic for
analyze.sec_alloc_perf_attribution.

For each subject code, cross-joins with all benchmark indices on date,
computes rolling close-price correlations, merges ETF-market liquidity
amounts, computes the 5-day MA of the liquidity ratio, and bulk-upserts
the resulting rows. Processes one subject at a time to bound memory.

Incremental mode: when ``target_dates`` is a non-empty set, only rows
whose date is in ``target_dates`` are upserted. Rolling correlations and
the MA5 ratio are still computed over the full per-(subject, benchmark)
history so the trailing windows are correct, but only target-date rows
survive to the upsert.

STEP DECOMPOSITION
==================

``build_and_insert`` orchestrates these smaller, individually-testable
steps (each is a pure-pandas operation with no object-dtype intermediates
so it can be swapped for cuDF later):

  1. ``_prepare_pivots``           — pivot benchmarks + ETF amounts to
                                      wide/long format ONCE per call.
  2. ``_filter_target_dates``      — DB-side skip for already-present
                                      target dates (safety net).
  3. ``_merge_subject_with_benchmarks`` — inner-join one subject's
                                      closes with all benchmarks on date.
  4. ``_attach_shared_weights``    — vectorized lookup of precomputed
                                      (subject, benchmark) overlap weights.
  5. ``_compute_rolling_correlations`` — vectorized ``df.rolling(N).corr(s)``
                                      against the wide benchmark pivot,
                                      one window at a time.
  6. ``_attach_etf_amounts``       — merge benchmark + subject ETF turnover.
  7. ``_compute_ma5_ratio``        — capped ratio + grouped rolling MA(5)
                                      via shared ``grouped_rolling_agg``.
  8. ``_filter_to_target_rows``    — incremental-mode row filter.
  9. ``sanitize_for_db_insert``    — shared NaN/inf/None sanitization.
"""
from __future__ import annotations

import datetime
from typing import Optional, Set

import numpy as np
import pandas as pd

from utils.build_commons import bulk_upsert_async, find_missing_dates

from analyze._common import (
    grouped_rolling_agg,
    sanitize_for_db_insert,
)
from analyze._common._cuDF import should_use_gpu
from analyze.sec_alloc_perf_attribution.config import (
    CORR_WINDOWS,
    RATIO_CAP,
    TABLE,
)


# ---------------------------------------------------------------------------
#  Step 1: pivot benchmarks + ETF amounts to wide/long format ONCE per call
# ---------------------------------------------------------------------------
def _prepare_pivots(
    index_closes: pd.DataFrame,
    etf_amount_by_index: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Pre-pivot benchmarks and ETF amounts to wide format.

    Returns (benchmark_close_wide, etf_amount_wide, etf_amount_long):
      - benchmark_close_wide: date x benchmark_code (close prices).
      - etf_amount_wide     : date x index_code (aggregate ETF turnover).
      - etf_amount_long     : long format (date, index_code, etf_amount).

    Both wide pivots are sorted by date. etf_amount_long is pre-built ONCE
    so each subject reuses it instead of recomputing stack+reset_index
    ~44 times. All three are empty frames when the source is empty.
    """
    benchmark_close_wide = (
        index_closes.pivot(index="date", columns="benchmark_code",
                           values="benchmark_close")
        .sort_index()
    )

    if not etf_amount_by_index.empty:
        etf_amount_wide = (
            etf_amount_by_index.pivot(index="date", columns="index_code",
                                      values="etf_amount")
            .sort_index()
        )
        etf_amount_long = etf_amount_wide.stack().reset_index()
        etf_amount_long.columns = ["date", "index_code", "etf_amount"]
        etf_amount_long["date"] = pd.to_datetime(
            etf_amount_long["date"]
        ).dt.date
    else:
        etf_amount_wide = pd.DataFrame()
        etf_amount_long = pd.DataFrame(
            columns=["date", "index_code", "etf_amount"]
        )

    return benchmark_close_wide, etf_amount_wide, etf_amount_long


# ---------------------------------------------------------------------------
#  Step 2: DB-side skip for already-present target dates (safety net)
# ---------------------------------------------------------------------------
async def _filter_target_dates(conn, target_dates):
    """Return target_dates minus dates already present in TABLE.

    The find_missing_analysis_dates pre-check in __main__.py already
    filters target_dates, but this catches any edge cases where dates
    were partially populated.
    """
    if target_dates is None or len(target_dates) == 0:
        return target_dates

    missing = await find_missing_dates(conn, TABLE, target_dates)
    n_already = len(target_dates) - len(missing)
    if n_already > 0:
        print(f"    -> skip check: {n_already:,} of {len(target_dates):,} "
              f"target dates already present in {TABLE} (skipped)",
              flush=True)
    return missing


# ---------------------------------------------------------------------------
#  Step 3: inner-join one subject's closes with all benchmarks on date
# ---------------------------------------------------------------------------
def _merge_subject_with_benchmarks(
    subject_closes: pd.DataFrame,
    index_closes: pd.DataFrame,
    sec_type: str,
) -> Optional[pd.DataFrame]:
    """Inner-merge one subject's closes with all benchmarks on date.

    Returns None when the merge is empty (no shared dates or, for index
    subjects, only the self-pair exists). For sec_type='index', the
    self-pair (code == benchmark_code) is excluded.
    """
    subject_codes = subject_closes["code"].unique()
    if len(subject_codes) != 1:
        raise ValueError(
            f"_merge_subject_with_benchmarks expects exactly one subject, "
            f"got {len(subject_codes)}"
        )
    subject_code = subject_codes[0]
    sub = subject_closes[subject_closes["code"] == subject_code].copy()
    merged = sub.merge(index_closes, on="date", how="inner")
    if merged.empty:
        return None

    if sec_type == "index":
        merged = merged[merged["code"] != merged["benchmark_code"]]
        if merged.empty:
            return None

    merged["sec_type"] = sec_type
    return merged


# ---------------------------------------------------------------------------
#  Step 4: vectorized lookup of precomputed (subject, benchmark) overlap weights
# ---------------------------------------------------------------------------
def _attach_shared_weights(
    merged: pd.DataFrame,
    shared_weights: dict,
    subject_code: str,
) -> pd.DataFrame:
    """Attach code_sec_shared_weight + benchmark_sec_shared_weight columns.

    ``shared_weights`` is a dict from fetch_shared_weights():
        {(subject_code, benchmark_code): (code_wt, bench_wt)}

    Lookup is vectorized via ``Series.map`` returning a tuple per row,
    then split into two columns. Shared weights come from the latest
    composition snapshot — same for all dates.
    """
    def _lookup_wt(benchmark_code):
        pair = shared_weights.get((subject_code, benchmark_code))
        return pair if pair is not None else (None, None)

    wt = merged["benchmark_code"].map(_lookup_wt)
    merged["code_sec_shared_weight"] = [w[0] for w in wt]
    merged["benchmark_sec_shared_weight"] = [w[1] for w in wt]
    return merged


# ---------------------------------------------------------------------------
#  Step 5: vectorized rolling correlations against the wide benchmark pivot
# ---------------------------------------------------------------------------
def _compute_rolling_correlations(
    merged: pd.DataFrame,
    subject_closes: pd.DataFrame,
    subject_code: str,
    benchmark_close_wide: pd.DataFrame,
) -> pd.DataFrame:
    """Compute rolling Pearson correlations for all CORR_WINDOWS.

    For each window N, ``corr_Nd`` = Pearson correlation between the
    subject's close prices and each benchmark's close prices over the
    trailing N trading days. Computed via pandas' vectorized
    ``df.rolling(N, min_periods=P).corr(series)`` against the wide
    benchmark pivot. min_periods = max(2N/3, 3) so up to 1/3 of the
    window can be NaN.

    GPU acceleration: when the cuDF router determines the GPU is
    worthwhile for this row count × column count (rolling_corr op_type
    — the slowest pandas rolling op, ~8s/M rows), the wide benchmark
    pivot + subject series are transferred to cuDF and the
    ``rolling().corr()`` runs on GPU. cuDF's rolling corr is ~53×
    faster than pandas on the RTX 5090. The wide frame (many benchmark
    columns × many dates) amortizes the H2D/D2H transfer across all
    benchmarks at once. For small subject universes (few benchmarks, few
    dates) the CPU path is faster and is selected automatically.
    """
    sub = subject_closes[subject_closes["code"] == subject_code]
    subject_close_series = (
        sub.set_index("date")["subject_close"].sort_index()
    )
    common_dates = subject_close_series.index.intersection(
        benchmark_close_wide.index
    )
    sub_aligned = subject_close_series.reindex(common_dates)
    bench_aligned = benchmark_close_wide.reindex(common_dates)

    # GPU routing: the wide bench_aligned frame is the right input for
    # the router — its row count (dates) × column count (benchmarks)
    # determines whether transfer + cuDF compute beats pandas. The
    # subject series is small (1 column) and transferred alongside.
    use_gpu = should_use_gpu(bench_aligned, op_type="rolling_corr")

    if use_gpu:
        import cudf  # type: ignore[import-untyped]
        # cuDF's rolling().corr() supports a wide DataFrame against a
        # single Series — same API as pandas. Transfer both once;
        # each window's corr is computed on GPU and brought back to
        # pandas as a wide frame, then stacked to long for the merge.
        g_bench = cudf.from_pandas(bench_aligned)
        g_sub = cudf.Series(sub_aligned.values, index=g_bench.index)
        for N in CORR_WINDOWS:
            min_p = max(N * 2 // 3, 3)
            g_corr_wide = g_bench.rolling(
                N, min_periods=min_p
            ).corr(g_sub)
            corr_wide = g_corr_wide.to_pandas()
            corr_long = corr_wide.stack().reset_index()
            corr_long.columns = ["date", "benchmark_code", f"corr_{N}d"]
            corr_long["date"] = pd.to_datetime(corr_long["date"]).dt.date
            merged = merged.merge(
                corr_long, on=["date", "benchmark_code"], how="left"
            )
        return merged

    # CPU path (pandas Cython).
    for N in CORR_WINDOWS:
        min_p = max(N * 2 // 3, 3)
        corr_wide = bench_aligned.rolling(
            N, min_periods=min_p
        ).corr(sub_aligned)
        corr_long = corr_wide.stack().reset_index()
        corr_long.columns = ["date", "benchmark_code", f"corr_{N}d"]
        # Normalize date to python date objects so the merge keys match.
        corr_long["date"] = pd.to_datetime(corr_long["date"]).dt.date
        merged = merged.merge(
            corr_long, on=["date", "benchmark_code"], how="left"
        )

    return merged


# ---------------------------------------------------------------------------
#  Step 6: merge benchmark + subject ETF turnover
# ---------------------------------------------------------------------------
def _attach_etf_amounts(
    merged: pd.DataFrame,
    etf_amount_long: pd.DataFrame,
    sec_type: str,
    subject_code: str,
) -> pd.DataFrame:
    """Attach benchmark_etf_trading_amount + code_etf_trading_amount.

    For ALL subjects: benchmark_etf_trading_amount = aggregate ETF
    turnover tracking the benchmark index on this date.

    For sec_type='index': code_etf_trading_amount = aggregate ETF
    turnover tracking the SUBJECT index (keyed on subject_code). Index
    subjects have no own row in etf_liquidity_margin, so the aggregate
    ETF turnover tracking the subject index is the correct amount.
    """
    if etf_amount_long.empty:
        merged["benchmark_etf_trading_amount"] = None
        if "code_etf_trading_amount" not in merged.columns:
            merged["code_etf_trading_amount"] = None
        return merged

    # benchmark_etf_trading_amount: merge on (date, benchmark_code=index_code).
    merged = merged.merge(
        etf_amount_long.rename(columns={
            "index_code": "benchmark_code",
            "etf_amount": "benchmark_etf_trading_amount",
        }),
        on=["date", "benchmark_code"], how="left",
    )

    # code_etf_trading_amount for index subjects: aggregate ETF turnover
    # tracking the subject index (keyed on subject_code).
    if sec_type == "index":
        subject_amt = (
            etf_amount_long[etf_amount_long["index_code"] == subject_code]
            .rename(columns={"etf_amount": "code_etf_trading_amount"})
            [["date", "code_etf_trading_amount"]]
        )
        # Drop any pre-existing column before merge (guard for determinism).
        if "code_etf_trading_amount" in merged.columns:
            merged = merged.drop(columns=["code_etf_trading_amount"])
        merged = merged.merge(subject_amt, on="date", how="left")

    return merged


# ---------------------------------------------------------------------------
#  Step 7: capped ratio + grouped rolling MA(5) via shared grouped_rolling_agg
# ---------------------------------------------------------------------------
def _compute_ma5_ratio(merged: pd.DataFrame) -> pd.DataFrame:
    """Compute etf_trading_amount_ratio_benchmark_to_code_ma5.

    Mirrors the SQL GENERATED ratio logic (NULL when either amount is
    NULL or zero), PLUS a cap at |ratio| < 1e6 to match the SQL column's
    NUMERIC(10,4) limit. Then compute rolling(5).mean() per benchmark_code
    group with min_periods=1 so the first 4 days get a partial average.

    Uses the shared ``grouped_rolling_agg`` helper (Cython-compiled
    groupby.rolling().mean() — no Python lambda callback per group).
    """
    bench_amt = pd.to_numeric(merged["benchmark_etf_trading_amount"], errors="coerce")
    code_amt = pd.to_numeric(merged["code_etf_trading_amount"], errors="coerce")
    with np.errstate(divide="ignore", invalid="ignore"):
        raw_ratio = bench_amt / code_amt
    merged["_ratio"] = np.where(
        bench_amt.isna() | code_amt.isna()
        | (bench_amt == 0) | (code_amt == 0)
        | (np.abs(raw_ratio) >= RATIO_CAP),
        np.nan,
        raw_ratio,
    )

    ma5 = grouped_rolling_agg(
        merged, "benchmark_code", "_ratio",
        window=5, min_periods=1, agg="mean",
    )
    merged["etf_trading_amount_ratio_benchmark_to_code_ma5"] = ma5
    merged = merged.drop(columns=["_ratio"])
    return merged


# ---------------------------------------------------------------------------
#  Step 8: incremental-mode row filter
# ---------------------------------------------------------------------------
def _filter_to_target_rows(
    merged: pd.DataFrame,
    target_dates: Optional[Set[datetime.date]],
    subject_code: str,
    subject_idx: int,
    n_subjects: int,
) -> pd.DataFrame:
    """Filter merged to only target_dates rows (incremental mode).

    Rolling correlations and the MA5 ratio have already been computed
    over the full history, so the trailing windows are correct. This
    filter just selects which rows survive to the upsert.
    """
    if target_dates is None or len(target_dates) == 0:
        return merged

    n_before = len(merged)
    merged = merged[merged["date"].isin(target_dates)].copy()
    if (subject_idx + 1) % 10 == 0 or (subject_idx + 1) == n_subjects:
        print(f"      {subject_code}: incremental filter "
              f"{len(merged):,} of {n_before:,} rows in target_dates",
              flush=True)
    return merged


# ---------------------------------------------------------------------------
#  Step 9: shared sanitize_for_db_insert + bulk_upsert_async (kept inline)
# ---------------------------------------------------------------------------
_OUT_COLS = [
    "code", "date", "sec_type", "benchmark_code",
    "code_sec_shared_weight", "benchmark_sec_shared_weight",
    "benchmark_etf_trading_amount", "code_etf_trading_amount",
    "etf_trading_amount_ratio_benchmark_to_code_ma5",
    "corr_5d", "corr_20d", "corr_60d", "corr_255d",
]
# String/non-numeric columns that must NOT be sanitized as numeric.
_NON_NUMERIC_COLS = {"code", "date", "sec_type", "benchmark_code"}


def _select_and_sanitize(merged: pd.DataFrame) -> list[dict]:
    """Select output columns and sanitize for asyncpg upsert."""
    out = merged[_OUT_COLS].copy()
    if out.empty:
        return []
    numeric_cols = [c for c in _OUT_COLS if c not in _NON_NUMERIC_COLS]
    return sanitize_for_db_insert(out, numeric_cols=numeric_cols, round_to=4)


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

    Processes one subject at a time to bound memory. Each subject runs
    through steps 3-9; steps 1-2 are computed once per call.

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
            are upserted (incremental mode). Rolling correlations and
            the MA5 ratio are computed over the full history for
            correctness, then the DataFrame is filtered before upsert.
            When None, all rows are upserted (full recompute).

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
    target_dates = await _filter_target_dates(conn, target_dates)
    if target_dates is not None and len(target_dates) == 0:
        print("    -> all target dates already present; nothing to do.",
              flush=True)
        return 0

    # Step 1: pre-pivot benchmarks + ETF amounts ONCE per call.
    benchmark_close_wide, _etf_amount_wide, etf_amount_long = _prepare_pivots(
        index_closes, etf_amount_by_index
    )

    total = 0
    subject_codes = sorted(subject_closes["code"].unique())

    for i, subject_code in enumerate(subject_codes):
        # Step 3: inner-join subject with all benchmarks on date.
        one_subject = subject_closes[subject_closes["code"] == subject_code]
        merged = _merge_subject_with_benchmarks(one_subject, index_closes, sec_type)
        if merged is None:
            continue

        # Step 4: attach shared weights (vectorized lookup).
        merged = _attach_shared_weights(merged, shared_weights, subject_code)

        # Step 5: rolling correlations for all windows.
        merged = _compute_rolling_correlations(
            merged, subject_closes, subject_code, benchmark_close_wide
        )

        # Step 6: attach ETF turnover (benchmark + subject for index).
        merged = _attach_etf_amounts(
            merged, etf_amount_long, sec_type, subject_code
        )

        # Step 7: capped ratio + grouped rolling MA(5).
        merged = _compute_ma5_ratio(merged)

        # Step 8: incremental-mode row filter.
        merged = _filter_to_target_rows(
            merged, target_dates, subject_code, i, n_subjects
        )

        # Step 9: select output cols + sanitize + upsert.
        rows = _select_and_sanitize(merged)
        if not rows:
            continue

        n = await bulk_upsert_async(
            conn, TABLE, rows,
            key_columns=["code", "date", "sec_type", "benchmark_code"],
            batch_size=1000,
        )
        total += n
        if (i + 1) % 10 == 0 or (i + 1) == n_subjects:
            print(f"    [{i + 1}/{n_subjects}] {subject_code}: {len(rows):,} rows "
                  f"(cumulative: {total:,})", flush=True)

    return total
