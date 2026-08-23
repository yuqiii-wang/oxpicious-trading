"""OI stats — put/call OI ratio correlation stats per expiry group
(analysis.options_oi_stats).

The plain (unweighted) put/call ratio lives here; its moneyness-aware
refinement — the delta-weighted put/call ratio — is the greek_delta
skew data source (greek_delta.py).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from _common.df_utils import grouped_rolling_agg
from analyze.options.compute._shared import (
    _compute_mean_expiry_dates,
    _expanding_corr,
)


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
