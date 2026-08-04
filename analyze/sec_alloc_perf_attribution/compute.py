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
"""
from __future__ import annotations

import datetime
from typing import Optional, Set

import numpy as np
import pandas as pd

from utils.build_commons import bulk_upsert_async

from analyze.sec_alloc_perf_attribution.config import (
    CORR_WINDOWS,
    RATIO_CAP,
    TABLE,
)


async def build_and_insert(conn, subject_closes: pd.DataFrame,
                           index_closes: pd.DataFrame,
                           shared_weights: dict,
                           etf_amount_by_index: pd.DataFrame,
                           sec_type: str,
                           *,
                           target_dates: Optional[Set[datetime.date]] = None,
                           ) -> int:
    """For each subject code, cross-join with all indices on date, insert.

    Processes one subject at a time to bound memory.

    For sec_type='index': excludes self-pairs (code == benchmark_code).

    shared_weights: dict from fetch_shared_weights() —
      { (subject_code, benchmark_code): (code_wt, bench_wt) }

    etf_amount_by_index: DataFrame from fetch_etf_amount_by_index() with
      columns [index_code, date, etf_amount]. Used to populate:
        - benchmark_etf_trading_amount = etf_amount where index_code = benchmark_code
        - code_etf_trading_amount for sec_type='index' = etf_amount where
          index_code = subject_code (the aggregate ETF turnover tracking
          the subject index).

    For each (subject, benchmark, date) row, also computes the Pearson
    correlation between the subject's close prices and the benchmark's
    close prices over trailing N-day windows (N in {5, 20, 60, 255}).
    The correlations are computed via pandas' vectorized
    ``df.rolling(N).corr(series)`` against a wide-format benchmark-close
    pivot, so all benchmarks for one subject are handled in one pass.

    target_dates: when non-empty, only rows whose date is in this set are
      upserted (incremental mode). Rolling correlations and the MA5 ratio
      are computed over the full history for correctness, then the
      DataFrame is filtered to target_dates before upsert. When None,
      all rows are upserted (full recompute).

    Returns total rows inserted.
    """
    n_subjects = subject_closes["code"].nunique() if not subject_closes.empty else 0
    n_indices = (index_closes["benchmark_code"].nunique()
                 if not index_closes.empty else 0)
    print(f"    -> {n_subjects} {sec_type}s x {n_indices} indices "
          f"(cross-product on shared dates)", flush=True)

    if n_subjects == 0 or n_indices == 0:
        print("    -> no data to insert.", flush=True)
        return 0

    # Pre-pivot benchmark closes to wide format (date x benchmark_code) for
    # vectorized rolling-correlation computation.  Computed once per call and
    # reused for every subject.  Each column is one benchmark's close-price
    # history; the index is trading dates (date objects).
    benchmark_close_wide = (
        index_closes.pivot(index="date", columns="benchmark_code",
                           values="benchmark_close")
        .sort_index()
    )

    # Pre-pivot etf_amount_by_index to wide format (date x index_code) for
    # fast per-subject lookup via reindex.  Each column is one index's
    # aggregate-ETF-amount time series.  Computed once per call.
    if not etf_amount_by_index.empty:
        etf_amount_wide = (
            etf_amount_by_index.pivot(index="date", columns="index_code",
                                      values="etf_amount")
            .sort_index()
        )
    else:
        etf_amount_wide = pd.DataFrame()

    total = 0
    subject_codes = sorted(subject_closes["code"].unique())

    for i, subject_code in enumerate(subject_codes):
        sub = subject_closes[subject_closes["code"] == subject_code].copy()
        # Inner merge on date: pairs this subject's closes with every index's
        # close on the same trading day.
        merged = sub.merge(index_closes, on="date", how="inner")
        if merged.empty:
            continue

        # For index subjects, exclude self-pairs (subject == benchmark).
        if sec_type == "index":
            merged = merged[merged["code"] != merged["benchmark_code"]]
            if merged.empty:
                continue

        # Vectorized column assembly.
        merged["sec_type"] = sec_type

        # Look up shared weights for each (subject_code, benchmark_code) pair.
        # Shared weights are from latest composition snapshot — same for all dates.
        def _lookup_wt(benchmark_code):
            pair = shared_weights.get((subject_code, benchmark_code))
            return pair if pair is not None else (None, None)

        wt = merged["benchmark_code"].map(_lookup_wt)
        merged["code_sec_shared_weight"] = [w[0] for w in wt]
        merged["benchmark_sec_shared_weight"] = [w[1] for w in wt]

        # ---- Compute rolling close-price correlations -------------------
        # For each window N, corr_Nd = Pearson correlation between the
        # subject's close prices and each benchmark's close prices over the
        # trailing N trading days ending at each date.
        #
        # Vectorized via `df.rolling(N).corr(series)`:
        #   - sub_aligned: Series of subject closes, indexed by date
        #   - bench_aligned: DataFrame of all benchmark closes (date x
        #     benchmark_code), reindexed to the same dates as sub_aligned
        # The result is a date x benchmark_code DataFrame of per-benchmark
        # rolling correlations, which we stack to long format and merge
        # back into the per-(date, benchmark_code) `merged` frame.
        subject_close_series = (
            sub.set_index("date")["subject_close"].sort_index()
        )
        common_dates = subject_close_series.index.intersection(
            benchmark_close_wide.index
        )
        sub_aligned = subject_close_series.reindex(common_dates)
        bench_aligned = benchmark_close_wide.reindex(common_dates)

        for N in CORR_WINDOWS:
            # Use min_periods to allow some NaN values in the rolling window.
            # Default pandas behavior (min_periods=N) requires ALL N values
            # to be non-NaN, causing widespread NULLs when benchmarks have
            # even a single NaN close price within the trailing N days.
            # Setting min_periods to ~2/3 of the window allows correlation
            # computation when up to 1/3 of the data is missing.
            min_p = max(N * 2 // 3, 3)
            corr_wide = bench_aligned.rolling(N, min_periods=min_p).corr(sub_aligned)
            # Stack date x benchmark_code wide frame -> long format with
            # columns [date, benchmark_code, corr_Nd].
            corr_long = corr_wide.stack().reset_index()
            corr_long.columns = ["date", "benchmark_code", f"corr_{N}d"]
            # Normalize date to python date objects so the merge with
            # `merged` (whose date column is date objects) keys correctly.
            corr_long["date"] = pd.to_datetime(corr_long["date"]).dt.date
            merged = merged.merge(
                corr_long, on=["date", "benchmark_code"], how="left"
            )

        # ---- benchmark_etf_trading_amount + code_etf_trading_amount (for index subjects) -
        # For ALL subjects: benchmark_etf_trading_amount = aggregate ETF turnover
        # tracking the benchmark index on this date.  Looked up from the
        # wide pivot by reindexing to the merged frame's dates and
        # selecting the column matching each benchmark_code.
        #
        # For sec_type='index': code_etf_trading_amount = aggregate ETF turnover
        # tracking the SUBJECT index (column = subject_code in the wide
        # pivot). Index subjects have no own amount in etf_liquidity_margin,
        # so the aggregate ETF turnover tracking the subject index is the
        # correct code_etf_trading_amount.
        if not etf_amount_wide.empty:
            # Build a long-format DataFrame of (date, benchmark_code, etf_amount)
            # by stacking the wide pivot.  This is reused for both the
            # benchmark_etf_trading_amount lookup (keyed on benchmark_code) and the
            # code_etf_trading_amount lookup for index subjects (keyed on subject_code).
            etf_amount_long = (
                etf_amount_wide.stack().reset_index()
            )
            etf_amount_long.columns = ["date", "index_code", "etf_amount"]
            etf_amount_long["date"] = pd.to_datetime(
                etf_amount_long["date"]
            ).dt.date

            # benchmark_etf_trading_amount: merge on (date, benchmark_code=index_code).
            merged = merged.merge(
                etf_amount_long.rename(columns={
                    "index_code": "benchmark_code",
                    "etf_amount": "benchmark_etf_trading_amount",
                }),
                on=["date", "benchmark_code"], how="left",
            )

            # code_etf_trading_amount for index subjects: when sec_type='index',
            # set code_etf_trading_amount = aggregate ETF turnover tracking the
            # subject index (keyed on subject code).
            if sec_type == "index":
                subject_amt = (
                    etf_amount_long[etf_amount_long["index_code"] == subject_code]
                    .rename(columns={"etf_amount": "code_etf_trading_amount"})
                    [["date", "code_etf_trading_amount"]]
                )
                # Drop any pre-existing code_etf_trading_amount column before merge
                # (index_subject_returns has no such column, but this guard
                # keeps the merge deterministic).
                if "code_etf_trading_amount" in merged.columns:
                    merged = merged.drop(columns=["code_etf_trading_amount"])
                merged = merged.merge(subject_amt, on="date", how="left")
        else:
            # No ETF->index mapping data — both columns stay NULL.
            merged["benchmark_etf_trading_amount"] = None
            if "code_etf_trading_amount" not in merged.columns:
                merged["code_etf_trading_amount"] = None

        # ---- etf_trading_amount_ratio_benchmark_to_code_ma5 ------------------------
        # 5-trading-day moving average of the liquidity ratio
        # (benchmark_etf_trading_amount / code_etf_trading_amount). The ratio itself
        # is a GENERATED ALWAYS column in PostgreSQL (computed on insert
        # from the two amount columns), so it is NOT in the inserted
        # payload — but the MA5 is a regular column and MUST be computed
        # here because a moving average cannot be expressed as a GENERATED
        # column (it needs the preceding 4 rows).
        #
        # Mirror the SQL GENERATED ratio logic exactly (NULL when either
        # amount is NULL or zero), PLUS a cap at |ratio| < 1e6 to match
        # the SQL column's NUMERIC(10,4) limit (max 999,999.9999). Ratios
        # exceeding this cap are set to NULL in BOTH the SQL GENERATED
        # column and this MA5 computation, so the two stay consistent.
        # Without the cap, the GENERATED column would overflow on insert.
        #
        # Then compute rolling(5).mean() per (code, benchmark_code) group
        # with min_periods=1 so the first 4 days of each series get a
        # partial average instead of NULL.  transform() preserves the
        # original index so the result aligns back to `merged` regardless
        # of the sort order used inside groupby.
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
        # Sort by (benchmark_code, date) so rolling sees correct temporal order,
        # then transform back — the Series index is the sorted frame's index
        # (same labels as merged), so assignment aligns automatically.
        ma5 = (
            merged.sort_values(["benchmark_code", "date"])
            .groupby("benchmark_code", group_keys=False)["_ratio"]
            .transform(lambda s: s.rolling(5, min_periods=1).mean())
        )
        merged["etf_trading_amount_ratio_benchmark_to_code_ma5"] = ma5
        merged = merged.drop(columns=["_ratio"])

        # ---- Incremental filter: keep only target_dates rows ------------
        # Rolling correlations and the MA5 ratio have been computed over
        # the full per-(subject, benchmark) history, so the trailing
        # windows are correct. Now filter to target_dates so only those
        # rows are upserted.
        if target_dates is not None and len(target_dates) > 0:
            n_before = len(merged)
            merged = merged[merged["date"].isin(target_dates)].copy()
            if (i + 1) % 10 == 0 or (i + 1) == n_subjects:
                print(f"      {subject_code}: incremental filter "
                      f"{len(merged):,} of {n_before:,} rows in target_dates",
                      flush=True)

        out_cols = [
            "code", "date", "sec_type", "benchmark_code",
            "code_sec_shared_weight", "benchmark_sec_shared_weight",
            "benchmark_etf_trading_amount", "code_etf_trading_amount",
            "etf_trading_amount_ratio_benchmark_to_code_ma5",
            "corr_5d", "corr_20d", "corr_60d", "corr_255d",
        ]
        out = merged[out_cols].copy()
        if out.empty:
            continue
        # Round numeric columns to 4 decimal places — aligns inserted
        # precision with the SQL NUMERIC scale and strips float artifacts
        # from the rolling-sum / correlation math.  DataFrame.round skips
        # object columns (code, date, sec_type, benchmark_code,
        # allocation_effect), leaving them untouched.
        out = out.round(4)
        # Replace +/-inf with NaN — rolling correlation can produce inf when
        # one series has zero variance over the window (constant prices).
        # NUMERIC(10,6) rejects inf; sanitize before insertion.
        out = out.replace([np.inf, -np.inf], np.nan)
        # Replace NaN with None so asyncpg serializes them as SQL NULL.
        # astype(object) prevents pandas from converting None back to NaN.
        out = out.astype(object).where(pd.notna(out), None)
        rows = out.to_dict(orient="records")

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
