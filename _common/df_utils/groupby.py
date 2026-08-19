"""Grouped diff / shift helpers — single pandas path (cudf.pandas accelerated).

Consolidates two patterns that share the ``groupby(keys)[col].diff(N)``
/ ``groupby(keys)[col].shift(N)`` shape and were previously inlined
in analyze.mov_ave_spread:

  - ``grouped_diff``:  compute groupby().diff(periods) for one or more
    columns. Used by mov_ave_spread.helpers.compute_slopes_curvatures
    (12 diffs: 6 slopes + 6 curvatures) and mov_ave_spread.rsi.

  - ``grouped_shift``: compute groupby().shift(periods) for one or
    more columns. Used by mov_ave_spread.rsi and the last-extreme
    turning-point detection.

GPU ACCELERATION
================

When the process-level ``cudf.pandas`` hook is active, ALL pandas
operations transparently run on GPU via cuDF. There is NO manual
``import cudf`` / ``cudf.from_pandas()`` / ``to_pandas()`` branching.
The single pandas code path handles both CPU and GPU modes.

``should_use_gpu`` is called for LOGGING ONLY (awareness of whether
the data volume meets the GPU breakeven threshold).

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
    ``periods`` rows of each group.

    Single pandas code path — cudf.pandas accelerates transparently.
    The ``should_use_gpu`` router is consulted once for logging only.

    Args:
        df: DataFrame. Modified in place - new columns are added.
        group_keys: column name(s) defining the per-group partition.
        cols: column name or list of column names to diff.
        out_names: output column name or list. When None, uses
            ``f"{col}_diff"`` per input col. Length must match cols.
        periods: number of periods to shift for the diff (default 1).
        sort: if True, sort df by ``group_keys`` before groupby. The
            sorted frame preserves df's original index labels.

    Returns:
        The same ``df`` with the new diff columns added in place.
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

    # Log GPU decision for awareness (no branching).
    if should_use_gpu(work, op_type="groupby_diff"):
        print(f"    [cuDF router] {len(work):,} rows — groupby_diff (GPU-worthy)", flush=True)

    # Single code path — cudf.pandas accelerates transparently.
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

    Single pandas code path — cudf.pandas accelerates transparently.
    The ``should_use_gpu`` router is consulted once for logging only.

    Args:
        df: DataFrame. Modified in place - new columns are added.
        group_keys: column name(s) defining the per-group partition.
        cols: column name or list of column names to shift.
        out_names: output column name or list. When None, uses
            ``f"{col}_shift{periods}"`` per input col.
        periods: number of periods to shift (default 1; positive =
            shift forward in time, negative = shift backward).
        sort: if True, sort df by ``group_keys`` before groupby.

    Returns:
        The same ``df`` with the new shift columns added in place.
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

    # Log GPU decision for awareness (no branching).
    if should_use_gpu(work, op_type="groupby_shift"):
        print(f"    [cuDF router] {len(work):,} rows — groupby_shift (GPU-worthy)", flush=True)

    # Single code path — cudf.pandas accelerates transparently.
    for in_col, out_col in pairs:
        df[out_col] = (
            work.groupby(group_keys, sort=False)[in_col].shift(periods)
            .reindex(df.index)
        )

    return df
