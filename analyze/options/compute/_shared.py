"""Shared pure-pandas helpers for analyze.options.compute.*.

Group keys, open-expiry collapsing, rolling-suite primitives and the
expiring-group lookup helpers used by every data-source module
(skewness / oi_stats / walls / iv_skew / greek_*).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from _common.df_utils import should_use_gpu  # noqa: F401 — per project convention
from _common.df_utils import grouped_rolling_agg, host_array
from analyze.options.config import SKEWNESS_CROSS_WINDOW, SKEWNESS_WINDOWS

# Expiry group key for skewness aggregation and rolling (per option_type).
_EXPIRY_GROUP_KEY = ["option_type", "underlying_code", "expiry_date"]

# Non-expiry group key (for mean expiry computation, open group collapsing).
_EXPIRY_TYPE_UNDERLYING_KEY = ["option_type", "underlying_code"]

# |delta| targets for the iv_skew OTM wings: 25Δ (the standard market
# skew quote — liquid wing) and 10Δ (deeper wing: more crash-sensitive,
# steeper in panics, but sparser quotes; NULL when no near-target
# contract exists on the strike grid).
_DELTA_TARGET_25 = 0.25
_DELTA_TARGET_10 = 0.10
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

    All datetime casts run on RAW host arrays (the ``host_array`` unwrap
    first): chained ``.astype`` on the proxy-subclass ``to_numpy()``
    ndarray logs a cudf fallback per unit ("Unsupported dtype
    datetime64[us]" / "[D]").

    Args:
        df: DataFrame with columns option_type, underlying_code,
            expiry_date (datetime64 preferred; object dates converted).

    Returns:
        DataFrame [option_type, underlying_code, mean_expiry_date]
        (datetime64[ns]), one row per (option_type, underlying_code).
    """
    uniq = df[["option_type", "underlying_code", "expiry_date"]].drop_duplicates()
    expiry = host_array(uniq["expiry_date"].to_numpy())
    if expiry.dtype.kind != "M":
        # object (python date/datetime/None) input: ONE plain numpy cast
        # (the to_dt64 convention — pd.to_datetime would log a fallback).
        expiry = np.asarray(expiry, dtype="datetime64[us]")
    # ONE host numpy pass: datetime64 -> day ordinals (epoch days).
    ord_arr = expiry.astype("datetime64[D]").astype(np.int64)
    uniq = uniq.assign(_ord=ord_arr)
    means = (
        uniq.groupby(["option_type", "underlying_code"], sort=False)["_ord"]
        .mean()
        .reset_index()
    )
    mean_days = np.round(
        host_array(means["_ord"].to_numpy()).astype(np.float64)
    ).astype(np.int64)
    # datetime64[ns] (cuDF-native) — a [D] column assignment logs an
    # "Unsupported dtype datetime64[D]" __setitem__ fallback.
    means["mean_expiry_date"] = (
        mean_days.astype("datetime64[D]").astype("datetime64[ns]")
    )
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
        dataset_max_date = host_array(df["date"].to_numpy()).max()
    means = _mean_expiry_dates_frame(df)
    out = df.merge(
        means, on=["option_type", "underlying_code"], how="left",
    )
    # Host compare — Series > Timestamp on a proxied datetime column
    # logs a cudf __gt__ fallback; raw numpy compares dispatch nowhere.
    open_mask = (
        host_array(out["expiry_date"].to_numpy())
        > np.asarray(dataset_max_date, dtype="datetime64[us]")
    )
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
    # Host unwrap BEFORE the cast — the proxy-subclass ndarray's astype
    # logs a "Unsupported dtype datetime64[us]" fallback.
    dates = (
        host_array(means["mean_expiry_date"].to_numpy())
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

    Closed form per group: slope = (n·Σty − Σt·Σy) / (n·Σtt − (Σt)²),
    computed with one groupby-transform pass over [t, y, ty, tt] sums
    (same result as the former per-group ``apply`` of the two-moment
    regression — which logged a cudf fallback per group: the fast path
    cannot broadcast ``pd.Series(scalar, index=g.index)``). NaN groups:
    < 2 rows, zero time variance, or any NaN value (the NaN propagates
    through the sums, as it did through the former per-group means).

    Returns a DataFrame with target_col added (same length as df).
    """
    tmp = df[group_key + ["_t"]].copy()
    tmp["_t"] = tmp["_t"].astype("float64")
    tmp["_y"] = df[value_col].astype("float64")
    tmp["_ty"] = tmp["_t"] * tmp["_y"]
    tmp["_tt"] = tmp["_t"] * tmp["_t"]
    g = tmp.groupby(group_key, sort=False)
    # GPU groupby-transform sums, then ONE host unwrap per moment — all
    # downstream arithmetic is raw numpy (no proxy dispatch).
    n = host_array(g["_t"].transform("size").to_numpy()).astype("float64")
    st = host_array(g["_t"].transform("sum").to_numpy())
    sy = host_array(g["_y"].transform("sum").to_numpy())
    sty = host_array(g["_ty"].transform("sum").to_numpy())
    stt = host_array(g["_tt"].transform("sum").to_numpy())
    denom = n * stt - st * st
    numer = n * sty - st * sy
    with np.errstate(divide="ignore", invalid="ignore"):
        slope = np.where(
            (n >= 2.0) & (denom != 0.0), numer / denom, np.nan,
        )
    df[target_col] = slope
    return df


def _cross_indicators(group: pd.DataFrame, gap_col: str = "_gap") -> pd.DataFrame:
    """Per-day neutral-cross indicators for one expiry group (sorted by date).

    Returns a frame indexed like ``group`` with:
      crossed     — True when the gap's sign bucket changed vs the previous
                    day (a cross of the neutral anchor). False on the first
                    day and across NaN gaps.
      days_since  — trading days since the last cross (0 = crossed today);
                    equals the group's age in days while no cross has
                    happened yet.

    Vectorized (cumsum trick + maximum.accumulate).
    """
    gap = group[gap_col].to_numpy(dtype=np.float64)
    n = len(gap)
    if n == 0:
        return pd.DataFrame(
            {"crossed": pd.Series(dtype=bool), "days_since": pd.Series(dtype=np.int64)},
            index=group.index,
        )
    t = np.arange(n)

    above = np.nan_to_num(gap, nan=np.inf) >= 0
    # NaN gaps never count as a cross: force both sides False there.
    valid = ~np.isnan(gap)
    prev_above = np.roll(above, 1)
    prev_valid = np.roll(valid, 1)
    crossed = valid & prev_valid & (above != prev_above)
    crossed[0] = False

    # Days since last cross: distance from the most recent True position.
    last_cross = np.where(crossed, t, -1)
    days_since = t - np.maximum.accumulate(last_cross)

    return pd.DataFrame(
        {"crossed": crossed, "days_since": days_since},
        index=group.index,
    )


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
) -> pd.DataFrame:
    """Apply the rolling skewness stats suite to an expiry-group frame.

    Shared by all skew data sources (see skewness.py, iv_skew.py and the
    greek_* modules). Requires columns:
    _EXPIRY_GROUP_KEY + [date, underlying_close, skewness, skew_type].

    neutral: no-tilt anchor of the skew metric. gap = skewness - neutral;
      contrarian metrics track the gap vs this anchor; gap_maW = maW - neutral.
        1.0  — oi_moneyness / iv_smile (legacy anchor)
        0.5  — greek_delta (balanced put/call directional book)
        0.0  — greek_gamma / greek_vega (balanced call/put wings)

    Adds: pre-expiry contrarian metrics (cross_count_20d,
    days_since_last_cross, gap_side_share_20d), MA/STD (5/20/60) and
    gap-from-neutral stats, slopes.
    """
    agg = agg.copy()

    # ---- sort by expiry group and date for rolling ops ----------------
    agg = agg.sort_values(
        _EXPIRY_GROUP_KEY + ["date"]
    ).reset_index(drop=True)

    agg["_gap"] = agg["skewness"] - neutral

    # ---- pre-expiry contrarian metrics on _gap (skewness − neutral) ----
    #   cross_count_20d       — neutral crossings in the trailing
    #                           SKEWNESS_CROSS_WINDOW sessions (contested
    #                           positioning into expiry)
    #   days_since_last_cross — sessions since the last neutral crossing
    #                           (freshness of the last flip; 0 = crossed
    #                           today, group age while never crossed)
    #   gap_side_share_20d    — share of the trailing window at/above
    #                           neutral (one-sided crowding, in [0,1])
    ind = (
        agg.groupby(_EXPIRY_GROUP_KEY, sort=False, group_keys=False)
        # lambda (not apply kwargs — the cudf fast path rejects kwargs
        # and logs a fallback per call).
        .apply(lambda g: _cross_indicators(g, "_gap"))
        .reindex(agg.index)
    )
    agg["_crossed"] = ind["crossed"].astype(np.int64)
    agg["days_since_last_cross"] = ind["days_since"].astype(int)
    # NaN gaps stay NaN so they neither vote nor shrink the window's
    # denominator (rolling mean skips NaNs; min_periods=1).
    agg["_above_neutral"] = np.where(
        agg["_gap"].notna(), agg["_gap"] >= 0, np.nan
    )
    agg["cross_count_20d"] = grouped_rolling_agg(
        agg, _EXPIRY_GROUP_KEY, "_crossed",
        window=SKEWNESS_CROSS_WINDOW, min_periods=1, agg="sum",
    ).astype(int)
    agg["gap_side_share_20d"] = grouped_rolling_agg(
        agg, _EXPIRY_GROUP_KEY, "_above_neutral",
        window=SKEWNESS_CROSS_WINDOW, min_periods=1, agg="mean",
    )

    # Add sequential time index per expiry group for slope computation.
    agg["_t"] = agg.groupby(_EXPIRY_GROUP_KEY, sort=False).cumcount()

    # ---- rolling MA of skewness over 5/20/60 days ----------------------
    for w in SKEWNESS_WINDOWS:
        agg[f"skewness_ma{w}"] = grouped_rolling_agg(
            agg, _EXPIRY_GROUP_KEY, "skewness",
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

    # Clean up temporary columns
    agg = agg.drop(
        columns=["_gap", "_t", "_crossed", "_above_neutral"]
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
