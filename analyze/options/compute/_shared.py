"""Shared pure-pandas helpers for analyze.options.compute.*.

Group keys, open-expiry collapsing, rolling-suite primitives and the
expiring-group lookup helpers used by every data-source module
(skewness / oi_stats / walls / iv_skew / greek_*).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from _common.df_utils import should_use_gpu  # noqa: F401 — per project convention
from _common.df_utils import grouped_rolling_agg
from analyze.options.config import SKEWNESS_WINDOWS

# Expiry group key for skewness aggregation and rolling (per option_type).
_EXPIRY_GROUP_KEY = ["option_type", "underlying_code", "expiry_date"]

# Non-expiry group key (for mean expiry computation, open group collapsing).
_EXPIRY_TYPE_UNDERLYING_KEY = ["option_type", "underlying_code"]

# |delta| target for the 25-delta wings (iv_skew).
_DELTA_TARGET = 0.25
# OTM delta band: 0 < |delta| < 0.5 (iv_skew wings + greek_vega wings).
_DELTA_OTM_MAX = 0.5
# Minimum contracts for the 3rd-moment smile skewness (iv_skew).
_SMILE_MIN_CONTRACTS = 3


def _mean_expiry_dates_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Mean expiry_date per (option_type, underlying_code) — vectorized.

    The mean is taken over the DISTINCT expiry dates of each group
    (unweighted), rounded to the nearest day — the exact semantics of
    the former per-group ``apply(drop_duplicates().mean())`` +
    ``fromordinal(int(round(v)))`` map, computed on day-ordinal floats
    (an integer day offset between ordinal and epoch-day counts cancels
    exactly in the mean, so the rounded result is identical).

    Args:
        df: DataFrame with columns option_type, underlying_code,
            expiry_date (datetime64 preferred; object dates converted).

    Returns:
        DataFrame [option_type, underlying_code, mean_expiry_date]
        (datetime64[ns]), one row per (option_type, underlying_code).
    """
    uniq = df[["option_type", "underlying_code", "expiry_date"]].drop_duplicates()
    if not pd.api.types.is_datetime64_any_dtype(uniq["expiry_date"]):
        uniq["expiry_date"] = pd.to_datetime(uniq["expiry_date"])
    # ONE host numpy pass: datetime64 -> day ordinals (epoch days).
    ord_arr = (
        uniq["expiry_date"].to_numpy()
        .astype("datetime64[D]").astype(np.int64)
    )
    uniq = uniq.assign(_ord=ord_arr)
    means = (
        uniq.groupby(["option_type", "underlying_code"], sort=False)["_ord"]
        .mean()
        .reset_index()
    )
    mean_days = np.round(
        means["_ord"].to_numpy(dtype=np.float64)
    ).astype(np.int64)
    means["mean_expiry_date"] = mean_days.astype("datetime64[D]")
    return means[["option_type", "underlying_code", "mean_expiry_date"]]


def _apply_open_expiry_collapse(
    df: pd.DataFrame,
    dataset_max_date=None,
) -> pd.DataFrame:
    """Replace open groups' expiry_date with the mean expiry_date.

    Vectorized replacement for the former per-row
    ``.loc[open_mask].apply(lambda r: mean_map.get(...), axis=1)``
    collapse (B-A4): the per-(option_type, underlying_code) mean frame
    is LEFT-joined once and the open rows are swapped via a single
    ``where`` — zero per-element proxy dispatch.

    Args:
        df: DataFrame with columns date, option_type, underlying_code,
            expiry_date (datetime64).
        dataset_max_date: max(date) of the dataset; computed from ``df``
            when None.

    Returns:
        DataFrame with open expiry groups collapsed to the mean
        expiry_date (same row order and column set as the input).
    """
    if df.empty:
        return df
    if dataset_max_date is None:
        dataset_max_date = df["date"].max()
    means = _mean_expiry_dates_frame(df)
    out = df.merge(
        means, on=["option_type", "underlying_code"], how="left",
    )
    open_mask = out["expiry_date"] > dataset_max_date
    if open_mask.any():
        out["expiry_date"] = out["expiry_date"].where(
            ~open_mask, out["mean_expiry_date"]
        )
    return out.drop(columns=["mean_expiry_date"])


def _compute_mean_expiry_dates(df: pd.DataFrame) -> dict:
    """Compute mean expiry_date per (option_type, underlying_code).

    Vectorized (see _mean_expiry_dates_frame); kept as a dict for the
    callers that need a lookup map.

    Args:
        df: DataFrame with columns option_type, underlying_code, expiry_date.

    Returns:
        dict mapping (option_type, underlying_code) -> mean expiry_date
        (datetime.date).
    """
    means = _mean_expiry_dates_frame(df)
    keys = np.asarray(means["option_type"].to_numpy()).tolist()
    codes = np.asarray(means["underlying_code"].to_numpy()).tolist()
    dates = (
        means["mean_expiry_date"].to_numpy()
        .astype("datetime64[D]").astype(object).tolist()
    )
    return {
        (ot, uc): d for ot, uc, d in zip(keys, codes, dates)
    }


def _collapse_open_expiry_rows(df: pd.DataFrame) -> pd.DataFrame:
    """Replace expiry_date of open groups with the mean expiry_date.

    Mirrors the collapse applied in compute_options_walls: rows where
    expiry_date > dataset max date get the per-(option_type, underlying_code)
    mean expiry date.
    """
    return _apply_open_expiry_collapse(df.copy())


def _compute_full_history_slope(group: pd.DataFrame, col: str) -> float:
    """Compute linear regression slope of col vs time for one expiry group.

    Uses _t (sequential row number within group) as the time axis.
    slope = Σ((t-t̄)(y-ȳ)) / Σ((t-t̄)²).
    Returns NaN for groups with < 2 rows or zero time variance.
    """
    t = group["_t"].values.astype(np.float64)
    y = group[col].values.astype(np.float64)
    n = len(t)
    if n < 2 or np.std(t) == 0:
        return np.nan
    t_mean = np.mean(t)
    y_mean = np.mean(y)
    numerator = np.sum((t - t_mean) * (y - y_mean))
    denominator = np.sum((t - t_mean) ** 2)
    if denominator == 0:
        return np.nan
    return numerator / denominator


def _broadcast_slopes(
    df: pd.DataFrame,
    value_col: str,
    target_col: str,
    group_key: list[str],
) -> pd.DataFrame:
    """Compute per-group full-history slope of value_col and broadcast.

    Returns a DataFrame with target_col added (same length as df).
    """
    slopes = (
        df.groupby(group_key, sort=False)
        .apply(
            lambda g: pd.Series(
                _compute_full_history_slope(g, value_col),
                index=g.index,
            )
        )
        .reset_index(level=list(range(len(group_key))), drop=True)
        .sort_index()
    )
    df[target_col] = slopes.values
    return df


def _compute_cross_count(group: pd.DataFrame, gap_col: str = "_gap") -> pd.Series:
    """Cumulative count of sign changes in a gap column for an expiry group.

    For an expiry group sorted by date, tracks how many times the
    sign of (gap) changes from one day to the next.

    First day: 0 (no previous day to compare).
    Subsequent days:
      - gap >= 0 → skewness at/above its neutral anchor
      - gap <  0 → skewness below its neutral anchor
      - sign changed: counter +1; unchanged / NaN: keep previous value
    """
    gap = group[gap_col].values
    n = len(gap)
    counter = np.zeros(n, dtype=np.int64)

    for i in range(1, n):
        if np.isnan(gap[i]) or np.isnan(gap[i - 1]):
            counter[i] = counter[i - 1]
        elif (gap[i] >= 0) != (gap[i - 1] >= 0):
            counter[i] = counter[i - 1] + 1
        else:
            counter[i] = counter[i - 1]

    return pd.Series(counter, index=group.index)


def _expanding_corr(
    df: pd.DataFrame,
    group_key: list[str],
    col1: str,
    col2: str,
    min_periods: int = 10,
) -> pd.Series:
    """Compute whole-period (expanding window) Pearson correlation.

    For each group, computes the cumulative correlation between col1 and
    col2 from the first row to each subsequent row. Returns a Series
    aligned to df's index.

    Args:
        df: DataFrame sorted by group_key + [date].
        group_key: Group key columns.
        col1: First column for correlation.
        col2: Second column for correlation.
        min_periods: Minimum number of rows before returning a non-NaN corr.

    Returns:
        pd.Series with expanding window correlation values.
    """
    def _exp_corr(g):
        if len(g) < min_periods:
            return pd.Series(np.nan, index=g.index)
        # Use expanding window with min_periods
        corr_vals = (
            g[col1]
            .expanding(min_periods=min_periods)
            .corr(g[col2])
        )
        return corr_vals

    result = (
        df.groupby(group_key, sort=False)
        .apply(_exp_corr)
        .reset_index(level=list(range(len(group_key))), drop=True)
        .sort_index()
    )
    return result


def _rolling_skew_suite(
    agg: pd.DataFrame,
    neutral: float = 1.0,
    price_k: float | None = None,
) -> pd.DataFrame:
    """Apply the rolling skewness stats suite to an expiry-group frame.

    Shared by all skew data sources (see skewness.py, iv_skew.py and the
    greek_* modules). Requires columns:
    _EXPIRY_GROUP_KEY + [date, underlying_close, skewness, skew_type].

    neutral: no-tilt anchor of the skew metric. gap = skewness - neutral;
      cross counts track sign changes of the gap; gap_maW = maW - neutral.
        1.0  — oi_moneyness / iv_smile (legacy anchor)
        0.5  — greek_delta (balanced put/call directional book)
        0.0  — greek_gamma / greek_vega (balanced call/put wings)

    Price-space translation for the correlations:
        price_k=None (legacy basis): skew_price = S * skewness
          - oi_moneyness: S * E[M]       (deviation ±10% of spot)
          - iv_smile:     S * smile_skew (deviation ±tens of % of spot)
        price_k=k (greek_* basis): skew_price = S * (1 + (skew - neutral) * k)
          - dimensionless call-vs-put balances rebased into price space;
            k = GREEK_SKEW_PRICE_K (0.10) maps a full tilt of ±1 to ±10%
            of spot. (The 1%-per-unit display rebase used on the IV chart
            is frontend-only: as a correlation basis its ±0.3% deviations
            would make corr ≈ 1 trivially.)

    Adds: cross counts, MA/STD (5/20/60), gap-from-neutral stats, slopes,
    and whole-period price-space correlations with spot.
    """
    agg = agg.copy()

    # ---- sort by expiry group and date for rolling ops ----------------
    agg = agg.sort_values(
        _EXPIRY_GROUP_KEY + ["date"]
    ).reset_index(drop=True)

    agg["_gap"] = agg["skewness"] - neutral
    if price_k is None:
        agg["skew_price"] = agg["underlying_close"] * agg["skewness"]
    else:
        agg["skew_price"] = agg["underlying_close"] * (
            1.0 + agg["_gap"] * price_k
        )

    # ---- cumulative cross count of _gap (skewness − neutral) -----------
    agg["count_skewness_curve_crossed_spot"] = (
        agg.groupby(_EXPIRY_GROUP_KEY, sort=False)
        .apply(_compute_cross_count, gap_col="_gap")
        .reset_index(level=list(range(len(_EXPIRY_GROUP_KEY))), drop=True)
        .astype(int)
    )

    # Add sequential time index per expiry group for slope computation.
    agg["_t"] = agg.groupby(_EXPIRY_GROUP_KEY, sort=False).cumcount()

    # ---- rolling MA of skewness over 5/20/60 days ----------------------
    for w in SKEWNESS_WINDOWS:
        agg[f"skewness_ma{w}"] = grouped_rolling_agg(
            agg, _EXPIRY_GROUP_KEY, "skewness",
            window=w, min_periods=w, agg="mean",
        )

    # ---- rolling MA of underlying_close (spot) over 5/20/60 ------------
    for w in SKEWNESS_WINDOWS:
        agg[f"spot_ma{w}"] = grouped_rolling_agg(
            agg, _EXPIRY_GROUP_KEY, "underlying_close",
            window=w, min_periods=w, agg="mean",
        )

    # ---- rolling STD of skewness over 5/20/60 days ---------------------
    for w in SKEWNESS_WINDOWS:
        agg[f"skewness_std{w}"] = grouped_rolling_agg(
            agg, _EXPIRY_GROUP_KEY, "skewness",
            window=w, min_periods=w, agg="std", ddof=1,
        )

    # ---- gap_skewness_vs_spot_maW = skewness_maW - neutral -------------
    for w in SKEWNESS_WINDOWS:
        agg[f"gap_skewness_vs_spot_ma{w}"] = (
            agg[f"skewness_ma{w}"] - neutral
        )

    # ---- full-history slopes per expiry group --------------------------
    agg = _broadcast_slopes(
        agg, "_gap", "gap_skewness_vs_spot_slope",
        _EXPIRY_GROUP_KEY,
    )

    for w in SKEWNESS_WINDOWS:
        agg = _broadcast_slopes(
            agg, f"gap_skewness_vs_spot_ma{w}",
            f"gap_skewness_vs_spot_ma{w}_slope",
            _EXPIRY_GROUP_KEY,
        )

    # ---- whole-period (cumulative) correlation -------------------------
    # All correlations are computed in price space (see docstring). Only
    # MA-based correlations are stored (no daily correlation).
    for w in SKEWNESS_WINDOWS:
        agg[f"skew_price_ma{w}"] = grouped_rolling_agg(
            agg, _EXPIRY_GROUP_KEY, "skew_price",
            window=w, min_periods=w, agg="mean",
        )
        agg[f"corr_skewness_ma{w}_vs_spot_ma{w}"] = _expanding_corr(
            agg, _EXPIRY_GROUP_KEY,
            f"skew_price_ma{w}", f"spot_ma{w}",
            min_periods=w,
        ).values

    # Clean up temporary columns
    agg = agg.drop(
        columns=["_gap", "_t", "skew_price"]
        + [f"skew_price_ma{w}" for w in SKEWNESS_WINDOWS]
        + [f"spot_ma{w}" for w in SKEWNESS_WINDOWS]
    )
    return agg


def _finalize_skew_result(agg: pd.DataFrame) -> pd.DataFrame:
    """Select + order the SKEWNESS_RESULT_COLUMNS of a suite output."""
    from analyze.options.config import SKEWNESS_RESULT_COLUMNS

    result = agg[SKEWNESS_RESULT_COLUMNS].copy()
    result = result.sort_values(
        ["date", "option_type", "underlying_code", "expiry_date", "skew_type"]
    ).reset_index(drop=True)
    return result


def _nearest_row_metric(
    df: pd.DataFrame,
    dist_col: str,
    value_col: str,
    group_cols: list[str],
    out_col: str,
) -> pd.DataFrame:
    """Per group, take value_col of the row with the smallest dist_col.

    Vectorized via groupby().idxmin() + .loc reindex.

    Returns a DataFrame with group_cols + [out_col].
    """
    idx = df.groupby(group_cols, sort=False)[dist_col].idxmin()
    picked = df.loc[idx.dropna(), group_cols + [value_col]].copy()
    picked = picked.rename(columns={value_col: out_col})
    return picked.drop_duplicates(subset=group_cols)
