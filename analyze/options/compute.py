"""Pure pandas computation logic for analyze.options.

Persists per-expiry-group statistics stores:

1. Rolling skewness / moneyness stats (options_skewness_stats):
   Per contract-level moneyness (strike_price / underlying_close):
     skewness_ma5/20/60  — rolling MA of moneyness over 5/20/60 days
     skewness_std5/20/60 — rolling STD of moneyness over 5/20/60 days
     gap_skewness_vs_spot_maW = skewness_maW - 1
     gap_skewness_vs_spot_slope — full-history slope of (moneyness - 1)
     gap_skewness_vs_spot_maW_slope — full-history slope of gap_maW
     corr_skewness_vs_spot — 60-day rolling corr(moneyness, spot)
     count_skewness_curve_crossed_spot — cumulative count of sign changes
       in (skewness - 1) for this expiry group

2. OI stats (options_oi_stats):
   corr_put_call_ratio_vs_spot_ma5/20/60

GPU note: the pipeline is plain groupby/agg/cummax/rolling on ~1M rows; the
should_use_gpu import is included per project convention (the CPU path
handles this volume in seconds, so no cuDF-specific branch is needed).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from _common.df_utils import should_use_gpu  # noqa: F401 — per project convention
from _common.df_utils import grouped_rolling_agg
from analyze.options.config import (
    SKEWNESS_RESULT_COLUMNS,
    SKEWNESS_WINDOWS,
)

# Expiry group key for skewness aggregation and rolling.
_EXPIRY_GROUP_KEY = ["option_type", "underlying_code", "expiry_date"]

# Non-expiry group key (for mean expiry computation, open group collapsing).
_EXPIRY_TYPE_UNDERLYING_KEY = ["option_type", "underlying_code"]


def _compute_mean_expiry_dates(df: pd.DataFrame) -> dict:
    """Compute mean expiry_date per (option_type, underlying_code).

    Args:
        df: DataFrame with columns option_type, underlying_code, expiry_date.

    Returns:
        dict mapping (option_type, underlying_code) -> mean expiry_date.
    """
    # Convert to ordinal for numeric mean computation
    result = df.copy()
    result["_expiry_ordinal"] = result["expiry_date"].apply(
        lambda d: d.toordinal() if hasattr(d, "toordinal") else pd.Timestamp(d).toordinal()
    )

    mean_ordinals = (
        result.groupby(_EXPIRY_TYPE_UNDERLYING_KEY)["_expiry_ordinal"]
        .apply(lambda g: g.drop_duplicates().mean())
        .to_dict()
    )

    mean_dates = {}
    for k, v in mean_ordinals.items():
        if pd.notna(v):
            mean_dates[k] = pd.Timestamp.fromordinal(int(round(v))).date()
        else:
            mean_dates[k] = None
    return mean_dates


def _apply_open_expiry_collapse(
    agg: pd.DataFrame,
    dataset_max_date,
) -> pd.DataFrame:
    """Replace expiry_date for open groups with mean expiry_date.

    For each (option_type, underlying_code), compute the mean of all
    expiry dates. For rows where expiry_date > dataset_max_date (still
    open/not matured), set expiry_date to this mean. Then re-aggregate
    so collapsed groups are properly summed.

    Args:
        agg: DataFrame with columns date, option_type, underlying_code,
            expiry_date, w_sum, wm_sum, underlying_close.
        dataset_max_date: Maximum date in the dataset (open = expiry_date > this).

    Returns:
        DataFrame with open expiry groups collapsed to mean expiry_date.
    """
    if agg.empty:
        return agg

    # Compute mean expiry date per (option_type, underlying_code)
    mean_map = _compute_mean_expiry_dates(agg)

    # Identify open rows
    open_mask = agg["expiry_date"] > dataset_max_date

    if open_mask.any():
        # Replace expiry_date for open rows
        agg.loc[open_mask, "expiry_date"] = agg.loc[open_mask].apply(
            lambda r: mean_map.get((r["option_type"], r["underlying_code"]), r["expiry_date"]),
            axis=1,
        )

        # Re-aggregate: collapse rows that now share the same (date, option_type, underlying_code, expiry_date)
        agg = (
            agg.groupby(["date"] + _EXPIRY_GROUP_KEY, as_index=False, sort=False)
            .agg(
                w_sum=("w_sum", "sum"),
                wm_sum=("wm_sum", "sum"),
                underlying_close=("underlying_close", "first"),
            )
        )

    return agg


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
      - gap >= 0 → skewness at/above spot
      - gap <  0 → skewness below spot
      - sign changed (below↔above): counter +1
      - sign unchanged: keep previous value
      - NaN gap: keep previous value (no decision)
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


def compute_options_skewness_stats(df: pd.DataFrame) -> pd.DataFrame:
    """Compute per-expiry-group rolling skewness (OI-weighted moneyness) stats.

    Args:
        df: DataFrame from fetch.fetch_options_skewness_rows with columns:
            date, contract_code, option_type, underlying_code, expiry_date,
            strike_price, underlying_close, open_interest.

    Returns:
        DataFrame with SKEWNESS_RESULT_COLUMNS — one row per
        (date, option_type, underlying_code, expiry_date), with rolling
        MA/STD/gap/slope/correlation stats of OI-weighted mean moneyness.
        For open (non-matured) expiry groups, expiry_date is collapsed to
        the mean of all expiry dates per (option_type, underlying_code).
    """
    if df.empty:
        return pd.DataFrame(columns=SKEWNESS_RESULT_COLUMNS)

    out = df.copy()

    # ---- Step 1: compute contract-level moneyness and OI weights -------
    out["moneyness"] = out["strike_price"] / out["underlying_close"]
    out["w"] = out["open_interest"].clip(lower=1.0)
    out["wm"] = out["w"] * out["moneyness"]

    # ---- Step 2: aggregate to expiry-group level (OI-weighted mean) ----
    # For each (date, option_type, underlying_code, expiry_date), compute:
    #   OI-weighted mean moneyness = sum(w * moneyness) / sum(w)
    agg = (
        out.groupby(["date"] + _EXPIRY_GROUP_KEY, as_index=False, sort=False)
        .agg(
            w_sum=("w", "sum"),
            wm_sum=("wm", "sum"),
            underlying_close=("underlying_close", "first"),
        )
    )

    # ---- Step 2b: collapse open expiry groups to mean expiry_date ------
    dataset_max_date = agg["date"].max()
    agg = _apply_open_expiry_collapse(agg, dataset_max_date)

    agg["skewness"] = agg["wm_sum"] / agg["w_sum"]
    agg["_gap"] = agg["skewness"] - 1.0
    agg["skew_price"] = agg["skewness"] * agg["underlying_close"]

    # Remove intermediate columns
    agg = agg.drop(columns=["w_sum", "wm_sum"])

    if agg.empty:
        return pd.DataFrame(columns=SKEWNESS_RESULT_COLUMNS)

    # ---- Step 3: sort by expiry group and date for rolling ops ---------
    agg = agg.sort_values(
        _EXPIRY_GROUP_KEY + ["date"]
    ).reset_index(drop=True)

    # ---- Step 3b: cumulative cross count of _gap (skewness − 1) ----------
    agg["count_skewness_curve_crossed_spot"] = (
        agg.groupby(_EXPIRY_GROUP_KEY, sort=False)
        .apply(_compute_cross_count, gap_col="_gap")
        .reset_index(level=list(range(len(_EXPIRY_GROUP_KEY))), drop=True)
        .astype(int)
    )

    # Add sequential time index per expiry group for slope computation.
    agg["_t"] = agg.groupby(_EXPIRY_GROUP_KEY, sort=False).cumcount()

    # ---- Step 4: rolling MA of skewness over 5/20/60 days --------------
    for w in SKEWNESS_WINDOWS:
        agg[f"skewness_ma{w}"] = grouped_rolling_agg(
            agg, _EXPIRY_GROUP_KEY, "skewness",
            window=w, min_periods=w, agg="mean",
        )

    # ---- Step 4b: rolling MA of underlying_close (spot) over 5/20/60 ---
    for w in SKEWNESS_WINDOWS:
        agg[f"spot_ma{w}"] = grouped_rolling_agg(
            agg, _EXPIRY_GROUP_KEY, "underlying_close",
            window=w, min_periods=w, agg="mean",
        )

    # ---- Step 5: rolling STD of skewness over 5/20/60 days -------------
    for w in SKEWNESS_WINDOWS:
        agg[f"skewness_std{w}"] = grouped_rolling_agg(
            agg, _EXPIRY_GROUP_KEY, "skewness",
            window=w, min_periods=w, agg="std", ddof=1,
        )

    # ---- Step 6: gap_skewness_vs_spot_maW = skewness_maW - 1 -----------
    for w in SKEWNESS_WINDOWS:
        agg[f"gap_skewness_vs_spot_ma{w}"] = (
            agg[f"skewness_ma{w}"] - 1.0
        )

    # ---- Step 7: full-history slopes per expiry group ------------------
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

    # ---- Step 8: whole-period (cumulative) correlation -----------------
    # All correlations are computed in price space, not moneyness space.
    # skew_price = skewness * underlying_close translates moneyness back to
    # the same price units as the spot price, giving a meaningful
    # correlation between the skewness-adjusted price and spot.
    # Only MA-based correlations are stored (no daily correlation).
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

    # Clean up temporary skew_price MA columns
    agg = agg.drop(columns=[f"skew_price_ma{w}" for w in SKEWNESS_WINDOWS])

    # ---- Step 9: select and order result columns -----------------------
    result = agg[SKEWNESS_RESULT_COLUMNS].copy()
    result = result.sort_values(
        ["date", "option_type", "underlying_code", "expiry_date"]
    ).reset_index(drop=True)

    return result


# ---- Options OI stats computation ------------------------------------------

def compute_options_oi_stats(df: pd.DataFrame) -> pd.DataFrame:
    """Compute per-expiry-group OI put/call ratio correlation stats.

    For each (date, underlying_code, expiry_date) group, computes the
    put/call OI ratio (total put OI / total call OI) and its MA5/MA20/MA60,
    then calculates whole-period cumulative correlation between MA(ratio) and
    MA(underlying_close). Results are duplicated for both CALL and PUT rows
    since the ratio is a group-level metric.

    Open (non-matured) expiry groups are collapsed: all raw expiry dates
    within a (option_type, underlying_code) pair are replaced with the mean
    date, matching the expiry_identity FK convention.

    Args:
        df: DataFrame with columns:
            date, contract_code, option_type, underlying_code, expiry_date,
            open_interest, underlying_close.

    Returns:
        DataFrame with OI_RESULT_COLUMNS.
    """
    from analyze.options.config import OI_RESULT_COLUMNS, SKEWNESS_WINDOWS

    if df.empty:
        return pd.DataFrame(columns=OI_RESULT_COLUMNS)

    # ---- Apply open-expiry collapsing to match expiry_identity FK ----
    df = df.copy()
    dataset_max_date = df["date"].max()

    # Compute mean expiry date per (option_type, underlying_code)
    mean_map = _compute_mean_expiry_dates(df)

    # Identify open rows (expiry_date > max date in dataset)
    open_mask = df["expiry_date"] > dataset_max_date
    if open_mask.any():
        df.loc[open_mask, "expiry_date"] = df.loc[open_mask].apply(
            lambda r: mean_map.get((r["option_type"], r["underlying_code"]), r["expiry_date"]),
            axis=1,
        )

    # Group key for the ratio computation (option_type excluded — ratio
    # is a property of the expiry group, not a single option type).
    _RATIO_GROUP_KEY = ["underlying_code", "expiry_date"]

    # ---- Step 1: compute CALL and PUT OI per (date, underlying, expiry) --
    call_oi = (
        df[df["option_type"] == "CALL"]
        .groupby(["date"] + _RATIO_GROUP_KEY, as_index=False, sort=False)
        .agg(call_oi=("open_interest", "sum"))
    )
    put_oi = (
        df[df["option_type"] == "PUT"]
        .groupby(["date"] + _RATIO_GROUP_KEY, as_index=False, sort=False)
        .agg(put_oi=("open_interest", "sum"))
    )

    # Merge CALL and PUT OI
    merged = call_oi.merge(
        put_oi,
        on=["date"] + _RATIO_GROUP_KEY,
        how="outer",
    )
    merged["call_oi"] = merged["call_oi"].fillna(0)
    merged["put_oi"] = merged["put_oi"].fillna(0)

    # Put/call ratio
    merged["put_call_ratio"] = np.where(
        merged["call_oi"] > 0,
        merged["put_oi"] / merged["call_oi"],
        np.nan,
    )

    # Get underlying_close for each group
    spot_data = (
        df.groupby(["date"] + _RATIO_GROUP_KEY, as_index=False, sort=False)
        .agg(underlying_close=("underlying_close", "first"))
    )
    merged = merged.merge(spot_data, on=["date"] + _RATIO_GROUP_KEY, how="left")

    # ---- Step 2: sort for rolling ops -------------------------------
    merged = merged.sort_values(
        _RATIO_GROUP_KEY + ["date"]
    ).reset_index(drop=True)

    # ---- Step 3: rolling MA of put/call ratio and spot ---------------
    for w in SKEWNESS_WINDOWS:
        merged[f"pcr_ma{w}"] = grouped_rolling_agg(
            merged, _RATIO_GROUP_KEY, "put_call_ratio",
            window=w, min_periods=w, agg="mean",
        )
        merged[f"spot_ma{w}"] = grouped_rolling_agg(
            merged, _RATIO_GROUP_KEY, "underlying_close",
            window=w, min_periods=w, agg="mean",
        )

    # ---- Step 4: whole-period (cumulative) correlation ---------------
    for w in SKEWNESS_WINDOWS:
        merged[f"corr_put_call_ratio_vs_spot_ma{w}"] = _expanding_corr(
            merged, _RATIO_GROUP_KEY,
            f"pcr_ma{w}", f"spot_ma{w}",
            min_periods=w,
        ).values

    # ---- Step 5: duplicate for both CALL and PUT option types --------
    call_rows = merged.copy()
    call_rows["option_type"] = "CALL"
    put_rows = merged.copy()
    put_rows["option_type"] = "PUT"

    result = pd.concat([call_rows, put_rows], ignore_index=True)

    # ---- Step 6: select and order result columns ---------------------
    result = result[OI_RESULT_COLUMNS].copy()
    result = result.sort_values(
        ["date", "option_type", "underlying_code", "expiry_date"]
    ).reset_index(drop=True)

    return result
