"""Bulk rolling correlation computation with GPU acceleration.

GPU ARCHITECTURE (TWO-LAYER)
============================

Layer 1 — Process-level hook (``__init__.py``)
-----------------------------------------------
``compute/__init__.py`` calls ``maybe_enable_cudf_pandas(mode="auto")``
BEFORE any pandas import.  This patches the ``pandas`` module so that
ALL pandas operations run on GPU via cuDF transparently — including
``rolling().corr()``, which raw cuDF does NOT support directly.

Layer 2 — Per-operation router (``should_use_gpu``)
----------------------------------------------------
This module uses ``should_use_gpu`` from ``_common.df_utils`` to decide
whether the **current DataFrame** is large enough to benefit from GPU
(breakeven check + VRAM fit).  This router also handles logging so
the CLI shows a consistent ``[cuDF router] GPU: ...`` /
``[cuDF router] CPU: ...`` decision for every operation.

The router does NOT control the cudf.pandas hook — that's process-level.
It decides whether the op-level work is worth the GPU overhead; the
execution still uses the transparent cudf.pandas backend.

WHY ``pd.DataFrame`` TYPE HINTS (NOT ``cudf.DataFrame``)
-------------------------------------------------------

``cudf.pandas`` uses the **transparent proxy** model:

- ``pd.DataFrame`` IS the GPU-backed DataFrame when the hook is active
- Type hints describe the **public API contract** (what callers
  pass/expect), not the backend implementation
- The router uses ``len(df)`` / column count only — no data copy

DATE DTYPE REQUIREMENT
======================

``cudf.pandas`` requires ``datetime64[ns]`` for date columns (NOT
Python ``date`` objects which have object dtype).  This module
auto-converts date columns to ``datetime64`` at input and back to
``date`` objects at output.
"""
from __future__ import annotations

import datetime
import time
from typing import Optional, Set

import numpy as np
import pandas as pd

from analyze.sec_alloc_perf_attribution.config import CORR_WINDOWS


# ---------------------------------------------------------------------------
#  Step 5b: BULK rolling correlations (all subjects x all benchmarks)
# ---------------------------------------------------------------------------
def compute_rolling_correlations_bulk(
    subject_closes: pd.DataFrame,
    benchmark_close_wide: pd.DataFrame,
    subject_related_benchmarks: dict[str, set[str]],
    subject_codes: list[str],
    *,
    enable_gpu: Optional[bool] = None,
) -> pd.DataFrame:
    """Bulk rolling correlation computation for ALL subjects.

    Instead of computing rolling correlations per-subject (each with a
    wide frame too small to hit GPU breakeven), this function:

    1. Creates a subject-wide pivot: ``(dates x all_subjects)``
    2. For each benchmark, computes corr for ALL related subjects
       in a single vectorized pass
    3. Returns a long-format frame: ``(date, code, benchmark_code,
       corr_5d, corr_20d, corr_60d, corr_255d)``

    **GPU decision**: Delegates to ``should_use_gpu`` from
    ``_common.df_utils`` when ``enable_gpu`` is None.  Pass True/False
    to force a specific mode.

    Args:
        subject_closes: DataFrame [date (datetime64), code, subject_close].
        benchmark_close_wide: DataFrame (datetime64 index, benchmark_code cols).
        subject_related_benchmarks: {subject_code: set(benchmark_code)}.
        subject_codes: sorted list of all subject codes to process.
        enable_gpu: force GPU (True) or CPU (False).  When None, uses
            ``should_use_gpu(df, op_type="rolling_corr")`` which checks
            breakeven + VRAM fit via the shared router.

    Returns:
        DataFrame [date (object), code, benchmark_code, corr_5d, corr_20d,
        corr_60d, corr_255d].  Empty DataFrame when no work to do.
        Date column is converted back to Python date objects for the
        downstream pipeline.
    """
    t_start = time.time()

    # Step 0: Ensure date columns are datetime64 for cudf.pandas compat.
    if not _is_datetime64(subject_closes["date"]):
        subject_closes = subject_closes.copy()
        subject_closes["date"] = pd.to_datetime(subject_closes["date"])
    if not _is_datetime64(benchmark_close_wide.index):
        benchmark_close_wide = benchmark_close_wide.copy()
        benchmark_close_wide.index = pd.to_datetime(
            benchmark_close_wide.index
        )

    # Step 1: Create subject-wide pivot (dates x subjects).
    subject_wide = (
        subject_closes.pivot(
            index="date", columns="code", values="subject_close"
        )
        .sort_index()
    )

    # --- GPU decision via shared router ---
    gpu_used: bool
    if enable_gpu is not None:
        # Explicit override — skip the router.
        gpu_used = enable_gpu
        label = "GPU" if gpu_used else "CPU"
        print(
            f"    [corr_bulk] force={label} (enable_gpu={enable_gpu})",
            flush=True,
        )
    else:
        # Lazy import: keep this module pandas-free until the router
        # is actually needed.  The cudf.pandas hook is already active
        # at the process level (installed by compute/__init__.py).
        from _common.df_utils import should_use_gpu
        gpu_used = should_use_gpu(subject_wide, op_type="rolling_corr")

    # Build inverted index: benchmark_code -> list of related subject codes.
    benchmark_to_subjects: dict[str, list[str]] = {}
    for sc in subject_codes:
        related = subject_related_benchmarks.get(sc, set())
        for bc in related:
            benchmark_to_subjects.setdefault(bc, []).append(sc)

    # Only keep benchmarks that actually have related subjects.
    active_benchmarks = {
        bc for bc, subs in benchmark_to_subjects.items() if subs
    }
    if not active_benchmarks:
        return _empty_corr_frame()

    # Filter benchmark_close_wide to only active benchmarks.
    active_bench_cols = [
        c for c in benchmark_close_wide.columns if c in active_benchmarks
    ]
    bench_wide_active = benchmark_close_wide[active_bench_cols]

    # Step 2: Compute rolling corr for each window in bulk.
    all_corr_cols: list[str] = []
    corr_long_frames: list[pd.DataFrame] = []

    for N in CORR_WINDOWS:
        corr_col: str = f"corr_{N}d"
        all_corr_cols.append(corr_col)
        min_p: int = max(N * 2 // 3, 3)

        corr_window_results = _compute_window(
            subject_wide, bench_wide_active, benchmark_to_subjects,
            active_bench_cols, N, min_p, corr_col,
        )
        if corr_window_results:
            corr_long_frames.extend(corr_window_results)

    if not corr_long_frames:
        return _empty_corr_frame()

    # Step 3: Combine all corr results via concat + pivot.
    result = _combine_corr_results(corr_long_frames, all_corr_cols)

    # Step 4: Convert datetime64 date back to Python date objects for
    # downstream pipeline compatibility.
    if not result.empty and _is_datetime64(result["date"]):
        result = result.copy()
        result["date"] = result["date"].dt.date

    elapsed = time.time() - t_start
    n_benchmarks = len(active_bench_cols)
    n_subjects_total = len(subject_codes)
    total_cells = sum(
        len(benchmark_to_subjects.get(bc, [])) * len(subject_wide)
        for bc in active_bench_cols
    )
    gpu_label = "GPU" if gpu_used else "CPU"
    print(
        f"    [corr_bulk] {len(result):,} rows from "
        f"{n_subjects_total} subjects x {n_benchmarks} benchmarks "
        f"({total_cells:,} cells), elapsed: {elapsed:.2f}s",
        flush=True,
    )

    return result


# ---------------------------------------------------------------------------
#  Window computation (single path — cudf.pandas or CPU, transparent)
# ---------------------------------------------------------------------------
def _compute_window(
    subject_wide: pd.DataFrame,
    bench_wide_active: pd.DataFrame,
    benchmark_to_subjects: dict[str, list[str]],
    active_bench_cols: list[str],
    N: int,
    min_p: int,
    corr_col: str,
) -> list[pd.DataFrame]:
    """Compute rolling corr for one window.

    Uses standard pandas API.  When ``cudf.pandas`` hook is active,
    this runs on GPU transparently.  When not, it runs on CPU.

    Each call to ``.rolling().corr()`` on a subject_subset + bench_series
    pair is a single vectorized operation that cudf.pandas routes to
    cuDF when active.
    """
    results: list[pd.DataFrame] = []
    for bc in active_bench_cols:
        related_subjects = benchmark_to_subjects.get(bc, [])
        if not related_subjects:
            continue
        subject_subset = subject_wide[related_subjects]
        bench_series = bench_wide_active[bc]
        corr_wide = subject_subset.rolling(
            N, min_periods=min_p
        ).corr(bench_series)
        corr_long = corr_wide.stack().reset_index()
        corr_long.columns = ["date", "code", corr_col]
        corr_long["date"] = pd.to_datetime(corr_long["date"])
        corr_long["benchmark_code"] = bc
        results.append(corr_long)

    return results


# ---------------------------------------------------------------------------
#  Result combiner: concat all window results -> pivot to wide
# ---------------------------------------------------------------------------
def _combine_corr_results(
    corr_long_frames: list[pd.DataFrame],
    all_corr_cols: list[str],
) -> pd.DataFrame:
    """Combine per-window, per-benchmark corr frames into one wide frame.

    Each input frame has columns [date, code, corr_Nd, benchmark_code].
    We standardize (rename value col to "value", add "col_name"),
    concatenate, then pivot so each (date, code, benchmark_code) row
    has all corr columns side-by-side.
    """
    standardized: list[pd.DataFrame] = []
    for frame in corr_long_frames:
        key_cols = {"date", "code", "benchmark_code"}
        corr_val_cols = [c for c in frame.columns if c not in key_cols]
        for val_col in corr_val_cols:
            renamed = frame.rename(columns={val_col: "value"})
            renamed["col_name"] = val_col
            standardized.append(
                renamed[["date", "code", "benchmark_code",
                          "col_name", "value"]]
            )

    all_corr = pd.concat(standardized, ignore_index=True)

    # Pivot: col_name values become new column names.
    result = all_corr.pivot_table(
        index=["date", "code", "benchmark_code"],
        columns="col_name",
        values="value",
        aggfunc="first",
    )
    result.columns.name = None
    result = result.reset_index()

    # Ensure output column order.
    out_cols = ["date", "code", "benchmark_code"] + all_corr_cols
    existing_cols = [c for c in out_cols if c in result.columns]
    result = result[existing_cols].sort_values(
        ["code", "benchmark_code", "date"]
    ).reset_index(drop=True)

    return result


# ---------------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------------
def _is_datetime64(series: pd.Series) -> bool:
    """Check if a Series or Index has datetime64 dtype."""
    return pd.api.types.is_datetime64_any_dtype(series)


def _empty_corr_frame() -> pd.DataFrame:
    """Return an empty DataFrame with the standard corr output columns."""
    return pd.DataFrame(
        columns=["date", "code", "benchmark_code",
                 "corr_5d", "corr_20d", "corr_60d", "corr_255d"]
    )
