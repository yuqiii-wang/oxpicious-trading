"""Consolidated DataFrame utilities with cuDF acceleration.

This package is the single entry point for DataFrame operations that
should transparently use cuDF (GPU) when worthwhile and fall back to
pandas (CPU) otherwise. It consolidates three previously-scattered
pieces:

  1. **cuDF router** (``_router``, ``_detector``, ``_thresholds``) -
     the decision layer that checks (a) CUDA availability, (b) data
     volume above breakeven, and (c) VRAM fit. Previously in
     ``_common/_cuDF/``; re-exported here for new code. The legacy
     ``_common/_cuDF`` package remains as a thin shim for backward
     compatibility.

  2. **Rolling helpers** (``rolling``) - ``compute_moving_averages``
     (multi-window MA in one GPU pass, used by builds) and
     ``grouped_rolling_agg`` (single-window + single-column with
     configurable agg, used by analyzes). ``grouped_rolling_agg`` is
     re-exported here from ``analyze/_common/rolling.py``, which
     remains as a thin shim for backward compatibility.

  3. **Grouped diff / shift helpers** (``groupby``) -
     ``grouped_diff`` and ``grouped_shift`` for batched
     ``groupby().diff()`` / ``groupby().shift()`` on multiple columns
     in a single GPU pass. Previously inlined in
     ``analyze/mov_ave_spread/helpers.py`` (12 diffs for slopes +
     curvatures) and ``analyze/mov_ave_spread/rsi.py`` (delta + N-day
     shifts).

VALIDATED WORKLOADS (DB row counts at refactor time)
=====================================================

  stats.stock_basic_stats            6,803,605  -> GPU (rolling_mean)
  stats.stock_tech_stats             6,788,650  -> GPU (groupby_diff)
  stats.etf_basic_stats                971,002  -> GPU (rolling_mean)
  stats.index_basic_stats               648,692  -> GPU (rolling_mean)
  analysis.mov_ave_spreads_detail    1,527,468  -> GPU (merge + groupby_diff)
  analysis.mov_ave_rsi                  648,230  -> GPU (groupby_diff + ewm)
  analysis.sec_alloc_perf_attribution 43,692,229 -> GPU (rolling_corr)
  analysis.industry_sentiments          356,636  -> GPU (groupby_agg)

All production workloads are well above the conservative breakeven for
their respective op_types, so the router selects GPU whenever CUDA +
cuDF are available (Linux/WSL with NVIDIA driver). On CPU-only
machines (native Windows, macOS, WSL-CPU-only) the router short-
circuits to CPU and pandas is used directly.

USAGE
=====

    # Router - decide CPU vs GPU for a custom op.
    from _common.df_utils import should_use_gpu

    if should_use_gpu(df, op_type="rolling_std"):
        import cudf
        gdf = cudf.from_pandas(df)
        result = gdf.groupby(keys).rolling(W).std(ddof=0)
        result = result.to_pandas()
    else:
        result = df.groupby(keys).rolling(W).std(ddof=0)

    # Multi-window moving averages (builds).
    from _common.df_utils import compute_moving_averages

    df = compute_moving_averages(
        df, group_key="code", value_col="adj_close",
        windows=[5, 20, 60, 120, 255],
    )

    # Single-window grouped rolling agg (analyzes).
    from _common.df_utils import grouped_rolling_agg

    std_5d = grouped_rolling_agg(
        df, ["sec_type", "code"], "price",
        window=5, min_periods=5, agg="std", ddof=0,
    )

    # Batched groupby diff (12 diffs in one GPU pass).
    from _common.df_utils import grouped_diff

    grouped_diff(
        df, ["sec_type", "code"],
        cols=["price", "ma5", "ma20", "ma60", "ma120", "ma255"],
        out_names=["price_slope", "ma5_slope", "ma20_slope",
                   "ma60_slope", "ma120_slope", "ma255_slope"],
    )

MODULES
=======

  - ``_detector``  : CUDA / cuDF availability detection (cached).
  - ``_thresholds``: Breakeven row counts per operation type + VRAM.
  - ``_router``    : ``should_use_gpu()`` combining both + VRAM check.
  - ``_cudf_pandas``: process-level cudf.pandas activation
                     (``maybe_enable_cudf_pandas``) — must run BEFORE
                     pandas is first imported; this package's exports
                     are therefore LAZY (PEP 562) so importing
                     ``_common.df_utils`` never pulls pandas in.
  - ``rolling``    : ``compute_moving_averages`` + ``compute_emas`` +
                     ``grouped_rolling_agg``.
  - ``groupby``    : ``grouped_diff`` + ``grouped_shift`` (batched).

All exports are resolved lazily via module ``__getattr__``: the helper
submodules (``rolling`` / ``groupby`` / ``black_scholes``) import
pandas at module level, and eager re-exports here would import pandas
whenever the package itself is imported — breaking the
``_cudf_pandas`` import-order contract (the cudf.pandas hook must be
installed before pandas' first import).
"""
from __future__ import annotations

# name -> (module path, attribute). Resolved on first access only.
_EXPORTS: dict[str, tuple[str, str]] = {
    # Detector
    "GPUInfo": ("_common.df_utils._detector", "GPUInfo"),
    "detect_gpu": ("_common.df_utils._detector", "detect_gpu"),
    "is_gpu_available": ("_common.df_utils._detector", "is_gpu_available"),
    "get_gpu_info": ("_common.df_utils._detector", "get_gpu_info"),
    "reset_cache": ("_common.df_utils._detector", "reset_cache"),
    # Thresholds
    "OP_PROFILES": ("_common.df_utils._thresholds", "OP_PROFILES"),
    "breakeven_rows": ("_common.df_utils._thresholds", "breakeven_rows"),
    "estimate_df_memory_bytes": ("_common.df_utils._thresholds", "estimate_df_memory_bytes"),
    "fits_in_vram": ("_common.df_utils._thresholds", "fits_in_vram"),
    "list_thresholds": ("_common.df_utils._router", "list_thresholds"),
    # Router
    "GPUDecision": ("_common.df_utils._router", "GPUDecision"),
    "should_use_gpu": ("_common.df_utils._router", "should_use_gpu"),
    "decide_gpu": ("_common.df_utils._router", "decide_gpu"),
    # Process-level cudf.pandas activation
    "maybe_enable_cudf_pandas": ("_common.df_utils._cudf_pandas", "maybe_enable_cudf_pandas"),
    "activate": ("_common.df_utils._activate", "activate"),
    # Rolling helpers
    "compute_moving_averages": ("_common.df_utils.rolling", "compute_moving_averages"),
    "compute_emas": ("_common.df_utils.rolling", "compute_emas"),
    "grouped_rolling_agg": ("_common.df_utils.rolling", "grouped_rolling_agg"),
    # Grouped diff / shift helpers
    "grouped_diff": ("_common.df_utils.groupby", "grouped_diff"),
    "grouped_shift": ("_common.df_utils.groupby", "grouped_shift"),
    # Black-Scholes IV + Greeks (vectorized, CPU/GPU routed)
    "bs_price_greeks": ("_common.df_utils.black_scholes", "bs_price_greeks"),
    "solve_iv_newton": ("_common.df_utils.black_scholes", "solve_iv_newton"),
    "compute_iv_and_greeks": ("_common.df_utils.black_scholes", "compute_iv_and_greeks"),
}

__all__ = list(_EXPORTS)


def __getattr__(name: str):
    # LAZY (PEP 562): helper submodules import pandas at module level;
    # deferring their import keeps this package pandas-free so the
    # cudf.pandas import hook can still be installed by callers that
    # import the detector / _cudf_pandas first.
    try:
        module_path, attr = _EXPORTS[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    import importlib

    return getattr(importlib.import_module(module_path), attr)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
