"""Rolling aggregation helpers with cuDF acceleration.

Consolidates two patterns that share the same ``groupby(keys)[col]
.rolling(W, min_periods=P).agg(...)`` shape:

  - ``compute_moving_averages``: compute MULTIPLE MA windows (ma5,
    ma20, ma60, ma120, ma255) on a single value column in one pass.
    Used by builds (stock_tech_stats, etf, index_baseline) on the
    6.8M / 971K / 649K row source tables.

  - ``grouped_rolling_agg``: compute ONE window x ONE column with a
    configurable agg (mean / std / var / sum / count / min / max).
    Used by analyzes (mov_ave_spread std, sec_alloc_perf MA5,
    industry_sentiments/etf_contribution MA5+MA20).

Both use the cuDF router (``_common.df_utils._router.should_use_gpu``)
to decide CPU vs GPU per call. The decision is cached per
(op_type, n_rows) so repeated calls in a loop don't re-log.

GPU AMORTIZATION
================

``compute_moving_averages`` transfers the (group_key, value_col)
subset to VRAM ONCE and computes all MA windows from that single
transfer - this is the key benefit over calling
``grouped_rolling_agg`` once per window (which would re-transfer per
call). Use ``compute_moving_averages`` when you need multiple
windows on the same column; use ``grouped_rolling_agg`` when you
need a single window.

CALLER CONTRACT
===============

The caller MUST pre-sort ``df`` by ``[group_key, date_col]`` so that
rolling windows see correct temporal order within each group. Both
helpers preserve df's original index in their output alignment.
"""
from __future__ import annotations

import pandas as pd

from _common.df_utils._router import should_use_gpu


# ---------------------------------------------------------------------------
#  Multi-window moving averages (builds path)
# ---------------------------------------------------------------------------
def compute_moving_averages(
    df: pd.DataFrame,
    group_key: str,
    value_col: str,
    windows: list[int],
    *,
    min_periods: int = 1,
    round_to: int = 6,
    add_ratio: bool = True,
    ratio_window: int | None = None,
) -> pd.DataFrame:
    """Compute multiple rolling moving averages per group.

    Adds columns ``ma{window}`` for each window in ``windows``. When
    ``add_ratio`` is True, also adds ``ma{ratio_window}_ratio`` =
    ``(value_col / ma{ratio_window} - 1.0)``.

    Uses the cuDF router (``should_use_gpu``) to decide CPU vs GPU.
    The decision is based on the ``rolling_mean`` breakeven threshold
    (~100K rows conservative). When GPU is selected:

      1. Only the (group_key, value_col) columns are transferred to
         VRAM (object-dtype columns like python dates are skipped).
      2. All MA windows are computed on-device from the single
         transfer - amortizes PCIe cost across all windows.
      3. Results are transferred back and rounded on CPU.

    The caller MUST pre-sort ``df`` by ``[group_key, date_col]`` so
    that rolling windows see correct temporal order within each group.

    Args:
        df: DataFrame pre-sorted by [group_key, date]. Modified in
            place - MA columns are added directly.
        group_key: column to group by (e.g. "code").
        value_col: column to compute MAs on (e.g. "close",
            "adj_close").
        windows: list of window sizes (e.g. [5, 20, 60, 120, 255]).
        min_periods: minimum non-NaN values in the window for a
            result. Default 1 = partial MAs for the first W-1 rows
            (matches the build-script convention).
        round_to: decimal places to round MA values (default 6).
        add_ratio: if True, compute ``ma{ratio_window}_ratio``.
        ratio_window: which window to use for the ratio column.
            Default ``windows[0]`` (typically ma5_ratio).

    Returns:
        The same ``df`` with ``ma{window}`` columns (and optionally
        ``ma{ratio_window}_ratio``) added in place.

    USAGE
    =====

        from _common.df_utils import compute_moving_averages

        df = df.sort_values(["code", "date"]).reset_index(drop=True)
        df = compute_moving_averages(
            df, group_key="code", value_col="adj_close",
            windows=[5, 20, 60, 120, 255],
        )
        # df now has columns: ma5, ma20, ma60, ma120, ma255, ma5_ratio
    """
    if df.empty:
        for w in windows:
            df[f"ma{w}"] = pd.Series(dtype="float64")
        if add_ratio:
            rw = ratio_window or windows[0]
            df[f"ma{rw}_ratio"] = pd.Series(dtype="float64")
        return df

    rw = ratio_window or windows[0]

    # ---- GPU path (cuDF) ----
    # Transfer only the needed columns to VRAM. Object-dtype columns
    # (e.g. python date objects) are not cuDF-compatible and would
    # waste VRAM, so we never transfer them.
    if should_use_gpu(df, op_type="rolling_mean"):
        import cudf  # type: ignore[import-untyped]
        work_subset = df[[group_key, value_col]]
        gdf = cudf.from_pandas(work_subset)
        g_grp = gdf.groupby(group_key, sort=False)[value_col]
        for w in windows:
            g_result = g_grp.rolling(window=w, min_periods=min_periods).mean()
            # Strip the group-key level -> align by original index.
            g_result = g_result.reset_index(level=0, drop=True)
            df[f"ma{w}"] = g_result.to_pandas().reindex(df.index)
    # ---- CPU path (pandas) ----
    else:
        g = df.groupby(group_key, sort=False)[value_col]
        for w in windows:
            df[f"ma{w}"] = g.transform(
                lambda x, _w=w: x.rolling(window=_w, min_periods=min_periods).mean()
            )

    # Round + ratio (on CPU - cheap elementwise, avoids cuDF overhead).
    for w in windows:
        df[f"ma{w}"] = df[f"ma{w}"].round(round_to)
    if add_ratio:
        df[f"ma{rw}_ratio"] = (
            (df[value_col] / df[f"ma{rw}"]) - 1.0
        ).round(round_to)

    return df


# ---------------------------------------------------------------------------
#  Single-window grouped rolling aggregation (analyzes path)
# ---------------------------------------------------------------------------
def grouped_rolling_agg(
    df: pd.DataFrame,
    group_keys: str | list[str],
    col: str,
    window: int,
    *,
    min_periods: int | None = None,
    agg: str = "mean",
    ddof: int = 1,
    sort: bool = True,
) -> pd.Series:
    """Compute a grouped rolling aggregation, aligned to df's index.

    Consolidates the ``groupby(keys)[col].rolling(W, min_periods=P)
    .agg(...).reset_index(level=..., drop=True)`` pattern used across
    analyze scripts:

      - analyze.mov_ave_spread.helpers.compute_rolling_stds (std, ddof=0)
      - analyze.sec_alloc_perf_attribution.compute (MA5 mean)
      - analyze.industry_sentiments.etf_contribution (MA5 + MA20 mean)

    The ``reset_index(level=..., drop=True)`` step strips the group-key
    levels from the resulting MultiIndex Series so it aligns back to the
    original DataFrame by the preserved original index.

    GPU acceleration: when the cuDF router determines the GPU is
    worthwhile for this op_type + row count, the computation is
    performed on a cuDF DataFrame and the result is brought back to
    pandas. cuDF supports ``groupby().rolling().agg()`` natively.
    The decision is cached per (op_type, n_rows) so repeated calls
    (e.g. 5 rolling-std windows in compute_rolling_stds) only log once.

    Args:
        df: DataFrame sorted by ``group_keys + [date_col]`` (or will be
            sorted if ``sort=True``). The result aligns back to ``df``
            by the original index.
        group_keys: column name(s) defining the per-group partition.
            E.g. ``["sec_type", "code"]`` or ``"benchmark_code"``.
        col: column to aggregate.
        window: rolling window size (in rows).
        min_periods: minimum non-NaN values in the window for a result.
            Defaults to ``window`` (strict - NULL until full window).
        agg: aggregation name - "mean", "std", "var", "sum", "count",
            "median", "min", "max".
        ddof: delta degrees of freedom for std/var (0 = population,
            1 = sample). Ignored for other aggs.
        sort: if True, sort df by ``group_keys`` before groupby so
            rolling sees correct temporal order within each group. The
            sorted frame preserves df's original index labels (just
            reordered), so the result aligns back by index. Set False
            when df is already sorted by group_keys for a small speedup.

    Returns:
        pd.Series aligned to ``df``'s index (same length, same order
        after reindex). NaN where the window has insufficient non-NaN
        values.

    USAGE
    =====

        from _common.df_utils import grouped_rolling_agg

        std_5d = grouped_rolling_agg(
            df, ["sec_type", "code"], "price",
            window=5, min_periods=5, agg="std", ddof=0,
        )
        df["std_5days"] = std_5d
    """
    if isinstance(group_keys, str):
        group_keys = [group_keys]
    n_levels = len(group_keys)

    if min_periods is None:
        min_periods = window

    work = df.sort_values(group_keys) if sort else df

    # Map agg name to cuDF op_type for the router's breakeven lookup.
    # rolling mean/sum share a profile; std/var share another.
    op_type = {
        "mean": "rolling_mean", "sum": "rolling_sum",
        "std": "rolling_std", "var": "rolling_var",
        "median": "rolling_mean",  # approximate
        "min": "rolling_mean", "max": "rolling_mean", "count": "rolling_mean",
    }.get(agg, "default")

    if should_use_gpu(work, op_type=op_type):
        import cudf  # type: ignore[import-untyped]
        # Only transfer the columns we need (group_keys + col) to VRAM.
        # Other columns (e.g. object-dtype ``date`` with python date
        # objects) are not cuDF-compatible and would waste VRAM.
        needed = list(group_keys) + [col]
        work_subset = work[needed]
        gdf = cudf.from_pandas(work_subset)
        g_grp = gdf.groupby(group_keys, sort=False)[col]
        g_roller = g_grp.rolling(window=window, min_periods=min_periods)
        if agg in ("std", "var"):
            g_result = getattr(g_roller, agg)(ddof=ddof)
        else:
            g_result = getattr(g_roller, agg)()
        # Strip group-key levels -> align by original index, then to_pandas.
        g_result = g_result.reset_index(
            level=list(range(n_levels)), drop=True
        )
        result = g_result.to_pandas()
        return result.reindex(df.index)

    # CPU path (pandas Cython).
    grp = work.groupby(group_keys, sort=False)[col]
    roller = grp.rolling(window=window, min_periods=min_periods)

    if agg in ("std", "var"):
        result = getattr(roller, agg)(ddof=ddof)
    else:
        result = getattr(roller, agg)()

    # Strip the group-key levels from the MultiIndex, leaving only the
    # original index. The result then aligns back to `df` by index.
    result = result.reset_index(
        level=list(range(n_levels)), drop=True
    )
    # Reindex back to df's original order (sort may have reordered).
    return result.reindex(df.index)
