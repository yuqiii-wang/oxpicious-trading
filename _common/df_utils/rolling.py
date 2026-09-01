"""Rolling aggregation helpers — single pandas path (cudf.pandas accelerated).

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

Also provides ``compute_emas`` for exponential moving averages
(ema6 / ema10 / ema20 / ema60), computed via a shift-doubling
parallel-prefix discounted sum — pure multiplication/addition on whole
columns (no ewm primitive, no per-group loop), so it runs on GPU under
cudf.pandas (cuDF lacks grouped-ewm; pandas groupby.ewm falls back to
CPU — see analyze/mov_ave_spread/rsi.py for the same constraint on
Wilder EWM).

GPU ACCELERATION
================

When the process-level ``cudf.pandas`` hook is active (enabled at the
entry point via ``_common.df_utils._activate.activate()``), ALL pandas
operations transparently run on GPU via cuDF. There is NO manual
``import cudf`` / ``cudf.from_pandas()`` / ``to_pandas()`` branching
— the single pandas code path handles both CPU and GPU modes.

``should_use_gpu`` is called for LOGGING ONLY (awareness of whether
the data volume meets the GPU breakeven threshold). It does NOT
control code-path branching.

CALLER CONTRACT
===============

The caller MUST pre-sort ``df`` by ``[group_key, date_col]`` so that
rolling windows see correct temporal order within each group. Both
helpers preserve df's original index in their output alignment.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from _common.df_utils._router import should_use_gpu

# EMA truncation: window grows until d^window < 1e-12 (relative error bound)
_EMA_LOG_EPS: float = math.log(1e-12)


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

    Single pandas code path — when ``cudf.pandas`` is active, this
    runs on GPU transparently. The ``should_use_gpu`` router is
    consulted once for logging only (no branching).

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

    # Log GPU decision for awareness (no branching — cudf.pandas
    # handles the actual GPU/CPU routing internally). The router prints
    # the decision once per unique (op_type, shape) — no extra line here
    # (it duplicated the router print on every call).
    should_use_gpu(df, op_type="rolling_mean")

    # Grouped rolling via the same cudf-native pattern as
    # grouped_rolling_agg — a lambda transform (x.rolling(...).mean())
    # falls back to CPU per window under cudf.pandas
    # (SeriesGroupBy.transform: Aggregation has no attribute rolling).
    # The result carries a group-key MultiIndex level; strip it so the
    # values align back to df's original index.
    g = df.groupby(group_key, sort=False)[value_col]
    for w in windows:
        df[f"ma{w}"] = (
            g.rolling(window=w, min_periods=min_periods)
            .mean()
            .reset_index(level=0, drop=True)
            .reindex(df.index)
        )

    # Round + ratio (elementwise — cheap on both CPU and GPU).
    for w in windows:
        df[f"ma{w}"] = df[f"ma{w}"].round(round_to)
    if add_ratio:
        df[f"ma{rw}_ratio"] = (
            (df[value_col] / df[f"ma{rw}"]) - 1.0
        ).round(round_to)

    return df


# ---------------------------------------------------------------------------
#  Multi-window exponential moving averages (builds path)
# ---------------------------------------------------------------------------
def compute_emas(
    df: pd.DataFrame,
    group_key: str,
    value_col: str,
    spans: list[int],
    *,
    adjust: bool = False,
    round_to: int = 6,
) -> pd.DataFrame:
    """Compute multiple exponential moving averages per group.

    Adds columns ``ema{span}`` for each span in ``spans``, matching
    ``close.ewm(span=N, adjust=False).mean()`` per group (``adjust=False``
    = the industry-standard "recursive" EMA where the first observation
    seeds the EMA; ``adjust=True`` would weight early observations
    differently to correct for the warm-up bias).

    Implementation: fused CUDA kernel (builds/_algo_kernels/ema.cpp) —
    the exact recursion

        y_t = a*x_t + (1-a)*y_{t-1},   y_0 = x_0

    unrolled to  ``y_t = a*G_t + d^(t+1)*x0``  with  ``d = 1-a`` and the
    truncated discounted sum

        G_t = sum_{j=0}^{w-1} d^j * x_{t-j}

    computed in ONE kernel launch per span (each thread reduces its own
    ~w-term window, cache-resident). When the kernel bridge is
    unavailable (no cupy), falls back to the equivalent shift-doubling
    column path (log2(w) whole-column passes). Both match pandas
    ``ewm(adjust=False, min_periods=1)`` to <=1e-12 relative everywhere.
    Results are rounded to ``round_to`` decimals; the round is the only
    extra column pass.

    NaN-containing columns fall back to the pandas path (pandas skips
    NaN inside the recurrence; the discounted sum would propagate it).

    Args:
        df: DataFrame pre-sorted by [group_key, date]. Modified in
            place - EMA columns are added directly.
        group_key: column to group by (e.g. "code"). Values must form
            contiguous blocks (guaranteed by the caller pre-sort).
        value_col: column to compute EMAs on (e.g. "close",
            "adj_close"). Must be NaN-free (else pandas fallback).
        spans: list of EMA spans (e.g. [6, 10, 20, 60]). The span
            parameter maps to alpha = 2 / (span + 1).
        adjust: non-default adjust=True still routes to pandas ewm.
        round_to: decimal places to round EMA values (default 6).

    Returns:
        The same ``df`` with ``ema{span}`` columns added in place.

    USAGE
    =====

        from _common.df_utils import compute_emas

        df = df.sort_values(["code", "date"]).reset_index(drop=True)
        df = compute_emas(
            df, group_key="code", value_col="close",
            spans=[6, 10, 20, 60],
        )
        # df now has columns: ema6, ema10, ema20, ema60
    """
    if df.empty:
        for s in spans:
            df[f"ema{s}"] = pd.Series(dtype="float64")
        return df
    x_np = np.asarray(df[value_col], dtype=np.float64)
    if adjust or bool(np.isnan(x_np).any()):
        if adjust:
            print(f"    [EMA] adjust=True → pandas ewm fallback ({len(x_np):,} rows)", flush=True)
        else:
            n_nan = int(np.isnan(x_np).sum())
            print(f"    [EMA] {n_nan} NaN in '{value_col}' → pandas ewm fallback "
                  f"(discounted sum would propagate NaN)", flush=True)
        return _compute_emas_pandas(df, group_key, value_col, spans,
                                    adjust=adjust, round_to=round_to)

    x = df[value_col]
    codes = df[group_key]
    # 0-based position within each group + each group's first value.
    # ffill from the pos==0 row fills every later row of the (contiguous)
    # group with its own first value.
    pos = df.groupby(group_key, sort=False).cumcount()

    try:
        import cupy as cp

        from builds._algo_kernels import ema_adjust_false

        # zero-copy device views via __cuda_array_interface__ (cudf
        # proxies expose it; plain pandas Series does not -> TypeError
        # routes to the doubling fallback below). No host round-trip:
        # input stays device-resident, results are rounded on GPU and
        # assigned back as cupy arrays.
        if x.dtype != "float64":
            x = x.astype("float64")
        x_gpu = cp.asarray(x)
        pos_gpu = cp.asarray(pos)
        out_gpu = cp.empty(x_gpu.size, dtype=cp.float64)
        for s in spans:
            alpha = 2.0 / (s + 1.0)
            ema_adjust_false(x_gpu, pos_gpu, alpha=alpha, span=s,
                             out=out_gpu)
            df[f"ema{s}"] = cp.round(out_gpu, round_to)
        del x_gpu, pos_gpu, out_gpu
    except (ImportError, TypeError):
        # no cupy / cudf.pandas inactive -> shift-doubling column path
        # (same closed form via whole-column ops).
        x0 = x.where(pos == 0).ffill()
        for s in spans:
            alpha = 2.0 / (s + 1.0)
            d = max(1.0 - alpha, 1e-300)     # decay (guards span=1 → d=0)
            log_d = math.log(d)
            # smallest power-of-two window with d^window < 1e-12
            w_needed = math.ceil(_EMA_LOG_EPS / log_d)
            g = x
            window = 1
            dstep = d                        # invariant: dstep == d ** window
            while window < w_needed:
                same = (codes == codes.shift(window)).fillna(False)
                g = g + (g.shift(window) * dstep).where(same, 0.0)
                window <<= 1
                dstep *= dstep
            # exact adjust=False warm-up: y_t = a*G_t + d^(t+1)*x0
            seed = np.exp((pos + 1) * log_d) * x0
            df[f"ema{s}"] = (alpha * g + seed).round(round_to)

    return df


def _compute_emas_pandas(
    df: pd.DataFrame,
    group_key: str,
    value_col: str,
    spans: list[int],
    *,
    adjust: bool = False,
    round_to: int = 6,
) -> pd.DataFrame:
    """Legacy pandas ``groupby.ewm`` path (NaN-tolerant, adjust-aware).

    Falls back to CPU under cudf.pandas — only used when the value
    column contains NaN or adjust=True is requested.
    """
    # pandas groupby.ewm returns a MultiIndex Series (group_key level +
    # original index). Strip the group-key level and reindex back to
    # df's original index so the result aligns with the caller's frame.
    grp = df.groupby(group_key, sort=False)[value_col]
    for s in spans:
        result = (
            grp.ewm(span=s, adjust=adjust, min_periods=1)
            .mean()
            .reset_index(level=0, drop=True)
        )
        df[f"ema{s}"] = result.reindex(df.index).round(round_to)

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

    Single pandas code path — when ``cudf.pandas`` is active, this
    runs on GPU transparently. The ``should_use_gpu`` router is
    consulted once for logging only (no branching).

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

    if min_periods is None:
        min_periods = window

    work = df.sort_values(group_keys) if sort else df

    # Log GPU decision for awareness (no branching — cudf.pandas
    # handles the actual GPU/CPU routing internally).
    op_type = {
        "mean": "rolling_mean", "sum": "rolling_sum",
        "std": "rolling_std", "var": "rolling_var",
        "median": "rolling_mean",
        "min": "rolling_mean", "max": "rolling_mean", "count": "rolling_mean",
    }.get(agg, "default")

    # Log GPU decision for awareness (no branching — cudf.pandas
    # handles the actual GPU/CPU routing internally). The router prints
    # the decision once per unique (op_type, shape) — no extra line here.
    should_use_gpu(work, op_type=op_type)

    # Single code path — cudf.pandas accelerates transparently when active.
    grp = work.groupby(group_keys, sort=False)[col]
    roller = grp.rolling(window=window, min_periods=min_periods)

    if agg in ("std", "var"):
        result = getattr(roller, agg)(ddof=ddof)
    else:
        result = getattr(roller, agg)()

    # Strip the group-key levels from the MultiIndex, leaving only the
    # original index. The result then aligns back to `df` by index.
    result = result.reset_index(
        level=list(range(len(group_keys))), drop=True
    )
    # Reindex back to df's original order (sort may have reordered).
    return result.reindex(df.index)
