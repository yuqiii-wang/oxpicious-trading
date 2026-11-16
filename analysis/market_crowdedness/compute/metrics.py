"""analysis.market_crowdedness.compute.metrics — shared metric cores.

The compute primitives used by both the rolling and the pinned paths:
the scalar clamp, the host-side hot-set name join, the sanctioned host
percentile core and the GPU Pearson correlation via grouped rolling
sums. Pure pandas/cuDF — vectorized, whole-frame passes plus the
sanctioned host cores documented per function.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from _common.df_utils import grouped_rolling_agg, host_array

from analysis.market_crowdedness.config import (
    CORR_WINDOW,
    MIN_CORR_OBS,
    MIN_PCT_OBS,
    PCT_WINDOW,
)


def _clip01(s: pd.Series) -> pd.Series:
    """Clamp to [0, 1] with the cuDF-native scalar-bounds form."""
    return np.minimum(np.maximum(s, 0.0), 1.0)


def _build_top_stocks(
    hot: pd.DataFrame,
    agg: pd.DataFrame,
    names: dict[str, str],
) -> np.ndarray:
    """Host-boundary name join: per (sec_type, pool, date) comma-join the
    hot set's display names in ma60-amount order.

    The rows arrive sorted by (sec_type, pool_code, date, hot_rank) —
    hot_rank was assigned over an amt_ma60-desc sort — so one linear pass
    fills rank-indexed slots per group (bucket size = the group's hot_n);
    the result is a host object ndarray re-attached via assign (the
    _compute_seasonal season_qkey pattern — strings never ride the frame
    through sort/merge/concat).
    """
    sec_h = host_array(hot["sec_type"].to_numpy()).astype("int64")
    pool_h = host_array(hot["pool_code"].to_numpy())
    date_h = (host_array(hot["date"].to_numpy())
              .astype("datetime64[ns]").astype("int64"))
    rank_h = host_array(hot["hot_rank"].to_numpy()).astype("int64")
    hotn_h = host_array(hot["hot_n"].to_numpy()).astype("int64")
    stock_h = host_array(hot["stock_code"].to_numpy())

    buckets: dict[tuple[int, str, int], list] = {}
    for s, p, d, r, hn, sc in zip(sec_h, pool_h, date_h, rank_h, hotn_h,
                                  stock_h):
        key = (int(s), str(p), int(d))
        lst = buckets.get(key)
        if lst is None:
            lst = [None] * int(hn)
            buckets[key] = lst
        if r <= len(lst):
            lst[r - 1] = names.get(sc, sc)

    sec_a = host_array(agg["sec_type"].to_numpy()).astype("int64")
    pool_a = host_array(agg["pool_code"].to_numpy())
    date_a = (host_array(agg["date"].to_numpy())
              .astype("datetime64[ns]").astype("int64"))
    top_arr = np.empty(len(agg), dtype=object)
    for i in range(len(agg)):
        lst = buckets.get((int(sec_a[i]), str(pool_a[i]), int(date_a[i])))
        top_arr[i] = (
            ", ".join(x for x in lst if x is not None) if lst else ""
        )
    return top_arr


def _rolling_pillar_pcts(
    agg: pd.DataFrame,
    pillars: list[tuple[str, str]],
) -> pd.DataFrame:
    """Host-boundary trailing percentiles: per (sec_type, pool) group,
    each pillar's value read against its own trailing PCT_WINDOW
    pool-date rows — x_pct(t) = share of non-null window values ≤ x(t),
    requiring MIN_PCT_OBS non-null observations.

    ``pillars`` maps pillar column -> output pct column (concentration
    -> concentration_pct, flow_change -> flow_pct, ...).

    Rolling rank is a gather/index algorithm with no cuDF-native form,
    so this is the sanctioned host core (agg is the SMALL per-(pool,
    date) frame; every pool-pillar is one sliding_window_view compare —
    microseconds each). agg must be sorted by (sec_type, pool_code,
    date); the pct columns are re-attached via assign.
    """
    n = len(agg)
    sec_h = host_array(agg["sec_type"].to_numpy()).astype("int64")
    pool_h = host_array(agg["pool_code"].to_numpy())
    val_h = {
        src: np.asarray(host_array(agg[src].to_numpy()), dtype="float64")
        for src, _dst in pillars
    }
    out: dict[str, np.ndarray] = {
        dst: np.full(n, np.nan) for _src, dst in pillars
    }

    # Group boundaries over the contiguous (sec_type, pool_code) blocks.
    starts: list[int] = [0]
    for i in range(1, n):
        if sec_h[i] != sec_h[i - 1] or pool_h[i] != pool_h[i - 1]:
            starts.append(i)
    starts.append(n)

    pad = np.full(PCT_WINDOW - 1, np.nan)
    for k in range(len(starts) - 1):
        i0, i1 = starts[k], starts[k + 1]
        for src, dst in pillars:
            v = val_h[src][i0:i1]
            t_len = v.shape[0]
            if t_len == 0:
                continue
            # (t_len, PCT_WINDOW) trailing windows (NaN-padded head).
            windows = np.lib.stride_tricks.sliding_window_view(
                np.concatenate([pad, v]), PCT_WINDOW)
            valid = ~np.isnan(windows)
            # Compare at the STORAGE quantization (4dp): cuDF grouped
            # reductions wobble below 5e-5 run-to-run, and an unrounded
            # compare lets that wobble flip near-tie percentiles (the
            # 2026-11-14 nondeterminism probe). Reading quantized
            # pillars also makes the stored *_pct verifiable from the
            # stored raw pillars.
            windows_q = np.round(windows, 4)
            cur = np.round(v, 4)[:, None]
            n_valid = valid.sum(axis=1)
            n_le = ((windows_q <= cur) & valid).sum(axis=1)
            ok = (~np.isnan(v)) & (n_valid >= MIN_PCT_OBS)
            res = n_le / np.maximum(n_valid, 1)
            out[dst][i0:i1] = np.where(ok, res, np.nan)

    return agg.assign(**out)


def _corr_via_rolling_sums(slim: pd.DataFrame) -> pd.DataFrame:
    """Trailing-CORR_WINDOW Pearson correlation per (sec_type, pool,
    stock) between the stock's return (x) and the pool's leave-one-out
    equal-weight return (y) — whole-frame GPU algebra: five grouped
    rolling sums + one rolling count (cuDF-native), then the
    Pearson identity per row.

    x/y are pre-masked to their JOINT validity, so all six rolling
    results share one NaN pattern and the pair counts only days where
    both legs are defined. Returns slim with a `corr` column.
    """
    keys3 = ["sec_type", "pool_code", "stock_code"]
    joint = slim["x"].notna() & slim["y"].notna()
    slim["x"] = slim["x"].where(joint)
    slim["y"] = slim["y"].where(joint)
    slim["xy"] = slim["x"] * slim["y"]
    slim["x2"] = slim["x"] * slim["x"]
    slim["y2"] = slim["y"] * slim["y"]

    def _roll(col: str, agg: str) -> pd.Series:
        return grouped_rolling_agg(
            slim, keys3, col, CORR_WINDOW,
            min_periods=1, agg=agg, sort=False,
        )

    n = _roll("x", "count")
    sx = _roll("x", "sum")
    sy = _roll("y", "sum")
    sxy = _roll("xy", "sum")
    sxx = _roll("x2", "sum")
    syy = _roll("y2", "sum")

    num = n * sxy - sx * sy
    d1 = np.maximum(n * sxx - sx * sx, 0.0)
    d2 = np.maximum(n * syy - sy * sy, 0.0)
    den = np.sqrt(d1 * d2)
    # Undefined (zero-variance) windows -> NaN via the where-mask; a
    # np.maximum(den, 1.0) guard here would clamp EVERY small-variance
    # denominator (returns are ~1e-2 scale) and return the raw numerator.
    corr = num / den.where(den > 0.0)
    ok = (n >= MIN_CORR_OBS) & (den > 0.0)
    # |corr| > 1 + eps is num/den rounding garbage on a near-degenerate
    # window (the rolling_corr kernel's guard) — NaN, not a value.
    ok = ok & (np.abs(corr) <= 1.0 + 1e-9)
    slim["corr"] = np.where(ok, corr, np.nan)
    return slim.drop(columns=["xy", "x2", "y2"])
