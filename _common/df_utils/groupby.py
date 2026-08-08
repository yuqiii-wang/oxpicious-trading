"""Grouped diff / shift helpers with cuDF acceleration.

Consolidates two patterns that share the ``groupby(keys)[col].diff(N)``
/ ``groupby(keys)[col].shift(N)`` shape and were previously inlined
in analyze.mov_ave_spread:

  - ``grouped_diff``:  compute groupby().diff(periods) for one or more
    columns in a single GPU pass. Used by mov_ave_spread.helpers.
    compute_slopes_curvatures (12 diffs: 6 slopes + 6 curvatures) and
    mov_ave_spread.rsi (delta for gain/loss).

  - ``grouped_shift``: compute groupby().shift(periods) for one or
    more columns in a single GPU pass. Used by mov_ave_spread.rsi
    (gap_{W}days = price[t] - price[t-W] via shift) and the
    last-extreme turning-point detection.

GPU AMORTIZATION
================

Both helpers accept a LIST of (col, out_name) pairs so that multiple
diffs/shifts on the same DataFrame run on a SINGLE cuDF transfer.
This is the key benefit over calling a single-column helper in a
loop (which would re-transfer the frame per call). The cuDF router
is queried ONCE for the batch; if the GPU is selected, all columns
are computed on-device and brought back in one ``to_pandas()`` call.

CALLER CONTRACT
===============

The caller MUST pre-sort ``df`` by ``group_keys + [date_col]`` so
that diff/shift see correct temporal order within each group. Both
helpers preserve df's original index in their output alignment.
"""
from __future__ import annotations

import pandas as pd

from _common.df_utils._router import should_use_gpu


def _normalize_pairs(
    cols, out_names, n_cols: int
) -> list[tuple[str, str]]:
    """Normalize (cols, out_names) args to a list of (in, out) pairs."""
    if isinstance(cols, str):
        cols = [cols]
    if out_names is None:
        return [(c, f"{c}_diff") for c in cols]
    if isinstance(out_names, str):
        out_names = [out_names]
    if len(out_names) != n_cols:
        raise ValueError(
            f"out_names length {len(out_names)} != cols length {n_cols}"
        )
    return list(zip(cols, out_names))


def grouped_diff(
    df: pd.DataFrame,
    group_keys: str | list[str],
    cols,
    out_names=None,
    *,
    periods: int = 1,
    sort: bool = True,
) -> pd.DataFrame:
    """Compute ``groupby(group_keys)[col].diff(periods)`` for one or more
    columns, aligned to df's index.

    For each (col, out_name) pair, adds ``df[out_name] =
    groupby(group_keys)[col].diff(periods)``. NaN on the first
    ``periods`` rows of each group (no preceding value to diff
    against).

    GPU acceleration: when the cuDF router determines the GPU is
    worthwhile for this row count (``groupby_diff`` op_type, breakeven
    ~320K rows conservative), the entire batch of diffs runs on a
    single cuDF DataFrame. Object-dtype columns (e.g. python ``date``
    objects) are dropped before the GPU transfer and restored after -
    they are only needed for sorting (which the caller is expected to
    have done) and would waste VRAM / break cuDF's dtype rules.

    Args:
        df: DataFrame. Modified in place - new columns are added.
        group_keys: column name(s) defining the per-group partition.
        cols: column name or list of column names to diff.
        out_names: output column name or list. When None, uses
            ``f"{col}_diff"`` per input col. Length must match cols.
        periods: number of periods to shift for the diff (default 1).
        sort: if True, sort df by ``group_keys`` before groupby. The
            sorted frame preserves df's original index labels, so the
            result aligns back by index. Set False when df is already
            sorted by group_keys for a small speedup.

    Returns:
        The same ``df`` with the new diff columns added in place.

    USAGE
    =====

        from _common.df_utils import grouped_diff

        # Compute 12 diffs in one GPU pass (6 slopes + 6 curvatures).
        grouped_diff(
            df, ["sec_type", "code"],
            cols=["price", "ma5", "ma20", "ma60", "ma120", "ma255"],
            out_names=["price_slope", "ma5_slope", "ma20_slope",
                       "ma60_slope", "ma120_slope", "ma255_slope"],
        )
        # Then curvature = diff of slope:
        grouped_diff(
            df, ["sec_type", "code"],
            cols=["price_slope", "ma5_slope", "ma20_slope",
                  "ma60_slope", "ma120_slope", "ma255_slope"],
            out_names=["price_curvature", "ma5_curvature",
                       "ma20_curvature", "ma60_curvature",
                       "ma120_curvature", "ma255_curvature"],
        )
    """
    if isinstance(group_keys, str):
        group_keys = [group_keys]
    if isinstance(cols, str):
        cols = [cols]
    pairs = _normalize_pairs(cols, out_names, len(cols))

    if df.empty:
        for _in_col, out_col in pairs:
            df[out_col] = pd.Series(dtype="float64")
        return df

    work = df.sort_values(group_keys) if sort else df

    if should_use_gpu(work, op_type="groupby_diff"):
        import cudf  # type: ignore[import-untyped]
        # Only transfer the group_keys + input cols to VRAM. Other
        # columns (e.g. object-dtype ``date``, wide numeric columns not
        # being diffed) would waste VRAM. cuDF's groupby.diff is
        # computed on this minimal subset and the new columns are
        # brought back to pandas, then attached to df by index.
        needed = list(group_keys) + [in_col for in_col, _ in pairs]
        gdf = cudf.from_pandas(work[needed])
        for in_col, out_col in pairs:
            gdf[out_col] = (
                gdf.groupby(group_keys, sort=False)[in_col].diff(periods)
            )
        # Bring back only the new output columns (as a DataFrame).
        out_cols = [out_col for _, out_col in pairs]
        out_df = gdf[out_cols].to_pandas()
        # Align by index - sort_values preserves df's original index
        # labels (just reordered), so reindex restores df's order.
        for out_col in out_cols:
            df[out_col] = out_df[out_col].reindex(df.index)
    else:
        # CPU path (pandas Cython).
        for in_col, out_col in pairs:
            df[out_col] = (
                work.groupby(group_keys, sort=False)[in_col].diff(periods)
                .reindex(df.index)
            )

    return df


def grouped_shift(
    df: pd.DataFrame,
    group_keys: str | list[str],
    cols,
    out_names=None,
    *,
    periods: int = 1,
    sort: bool = True,
) -> pd.DataFrame:
    """Compute ``groupby(group_keys)[col].shift(periods)`` for one or more
    columns, aligned to df's index.

    For each (col, out_name) pair, adds ``df[out_name] =
    groupby(group_keys)[col].shift(periods)``. NaN on the first
    ``periods`` rows of each group (positive periods) or last
    ``|periods|`` rows (negative periods).

    GPU acceleration: same as ``grouped_diff`` - when the cuDF router
    determines the GPU is worthwhile for this row count
    (``groupby_shift`` op_type, breakeven ~320K rows conservative),
    the entire batch of shifts runs on a single cuDF DataFrame.

    Args:
        df: DataFrame. Modified in place - new columns are added.
        group_keys: column name(s) defining the per-group partition.
        cols: column name or list of column names to shift.
        out_names: output column name or list. When None, uses
            ``f"{col}_shift{periods}"`` per input col (e.g.
            ``price_shift2``). Length must match cols.
        periods: number of periods to shift (default 1; positive =
            shift forward in time, negative = shift backward).
        sort: if True, sort df by ``group_keys`` before groupby. The
            sorted frame preserves df's original index labels, so the
            result aligns back by index. Set False when df is already
            sorted by group_keys for a small speedup.

    Returns:
        The same ``df`` with the new shift columns added in place.

    USAGE
    =====

        from _common.df_utils import grouped_shift

        # Compute price[t-2] and price[t-3] for N-day gap returns.
        grouped_shift(
            df, ["sec_type", "code"], "price",
            out_names=["price_prev2", "price_prev3"],
            # Note: this shifts by 1 only; for different periods call
            # once per period, or use the periods param with one col.
        )
        # Better - one call per period when periods differ:
        df["price_prev2"] = grouped_shift(
            df, ["sec_type", "code"], ["price"],
            out_names=["price_prev2"], periods=2,
        )["price_prev2"]
    """
    if isinstance(group_keys, str):
        group_keys = [group_keys]
    if isinstance(cols, str):
        cols = [cols]
    if out_names is None:
        suffix = f"_shift{periods}" if periods != 1 else "_shift"
        out_names = [f"{c}{suffix}" for c in cols]
    elif isinstance(out_names, str):
        out_names = [out_names]
    if len(out_names) != len(cols):
        raise ValueError(
            f"out_names length {len(out_names)} != cols length {len(cols)}"
        )
    pairs = list(zip(cols, out_names))

    if df.empty:
        for _in_col, out_col in pairs:
            df[out_col] = pd.Series(dtype="float64")
        return df

    work = df.sort_values(group_keys) if sort else df

    if should_use_gpu(work, op_type="groupby_shift"):
        import cudf  # type: ignore[import-untyped]
        # Only transfer the group_keys + input cols to VRAM (same
        # minimal-subset rationale as grouped_diff).
        needed = list(group_keys) + [in_col for in_col, _ in pairs]
        gdf = cudf.from_pandas(work[needed])
        for in_col, out_col in pairs:
            gdf[out_col] = (
                gdf.groupby(group_keys, sort=False)[in_col].shift(periods)
            )
        out_cols = [out_col for _, out_col in pairs]
        out_df = gdf[out_cols].to_pandas()
        for out_col in out_cols:
            df[out_col] = out_df[out_col].reindex(df.index)
    else:
        # CPU path (pandas Cython).
        for in_col, out_col in pairs:
            df[out_col] = (
                work.groupby(group_keys, sort=False)[in_col].shift(periods)
                .reindex(df.index)
            )

    return df
