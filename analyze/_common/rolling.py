"""Grouped rolling aggregation helper.

Consolidates the ``groupby(keys)[col].rolling(W, min_periods=P).agg(...)
.reset_index(level=..., drop=True)`` pattern used in 3 places:

  - analyze.mov_ave_spread.helpers.compute_rolling_stds (std, ddof=0)
  - analyze.sec_alloc_perf_attribution.compute (MA5 mean)
  - analyze.industry_sentiments.etf_contribution (MA5 + MA20 mean)

The ``reset_index(level=..., drop=True)`` step strips the group-key
levels from the resulting MultiIndex Series so it aligns back to the
original DataFrame by the preserved original index.

GPU acceleration: when the cuDF router (``analyze._common._cuDF``)
determines the GPU is worthwhile for this op_type + row count, the
computation is performed on a cuDF DataFrame and the result is brought
back to pandas. cuDF supports ``groupby().rolling().agg()`` natively.
The decision is cached per (op_type, n_rows) so repeated calls (e.g. 5
rolling-std windows in compute_rolling_stds) only log once.
"""
from __future__ import annotations

import pandas as pd

from analyze._common._cuDF import should_use_gpu


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

    Args:
        df: DataFrame sorted by ``group_keys + [date_col]`` (or will be
            sorted if ``sort=True``). The result aligns back to ``df``
            by the original index.
        group_keys: column name(s) defining the per-group partition.
            E.g. ``["sec_type", "code"]`` or ``"benchmark_code"``.
        col: column to aggregate.
        window: rolling window size (in rows).
        min_periods: minimum non-NaN values in the window for a result.
            Defaults to ``window`` (strict — NULL until full window).
        agg: aggregation name — "mean", "std", "var", "sum", "count",
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
        # Strip group-key levels → align by original index, then to_pandas.
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
