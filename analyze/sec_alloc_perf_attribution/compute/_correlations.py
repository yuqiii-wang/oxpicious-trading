"""Per-subject rolling correlation computation (backward compat).

This module provides the per-subject rolling correlation function
for backward compatibility and as a reference implementation.

For new code, prefer ``_gpu_corr.compute_rolling_correlations_bulk``
for bulk processing — it achieves GPU breakeven by processing
all subjects at once via the ``cudf.pandas`` hook.

When ``cudf.pandas`` is active (see ``__init__.py``), ALL pandas
operations in this module run on GPU transparently.
"""
from __future__ import annotations

import pandas as pd

from _common.df_utils import to_py_dates
from analyze.sec_alloc_perf_attribution.config import CORR_WINDOWS


def compute_rolling_correlations(
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

    GPU: when ``cudf.pandas`` hook is active, this runs on GPU
    transparently (no explicit cuDF conversion needed).
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

    for N in CORR_WINDOWS:
        min_p = max(N * 2 // 3, 3)
        corr_wide = bench_aligned.rolling(
            N, min_periods=min_p
        ).corr(sub_aligned)
        corr_long = corr_wide.stack().reset_index()
        corr_long.columns = ["date", "benchmark_code", f"corr_{N}d"]
        # date is datetime64 (pivot index) — convert to python dates with
        # ONE host numpy pass (a cudf-backed .dt.date falls back per
        # element; merged carries object dates so this keeps merge dtypes
        # aligned).
        to_py_dates(corr_long, ["date"])
        merged = merged.merge(
            corr_long, on=["date", "benchmark_code"], how="left"
        )

    return merged
