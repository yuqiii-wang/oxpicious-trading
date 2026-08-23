"""IV skew data sources (from stats.options_greeks implied vol).

1. compute_options_iv_skew_stats — pricing skew per expiry group
   (analysis.options_iv_skew_stats): ATM IV, 25-delta OTM wings,
   risk_reversal_25d (the professional skew metric), put/call skews,
   smile_skewness (OI-weighted 3rd moment of IV) + rolling suite on
   the risk reversal.

2. compute_options_iv_smile_corr_stats — rolling stats of the IV smile
   skewness (skew_type='iv_smile' in analysis.options_skewness_stats).

Premium information enters via implied_vol (calibrated from settle
prices with Black-76 and stored in stats.options_greeks). All output
IV/skew values are in vol points (percent).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from _common.df_utils import grouped_rolling_agg
from analyze.options.compute._shared import (
    _DELTA_OTM_MAX,
    _DELTA_TARGET,
    _EXPIRY_GROUP_KEY,
    _SMILE_MIN_CONTRACTS,
    _broadcast_slopes,
    _collapse_open_expiry_rows,
    _expanding_corr,
    _finalize_skew_result,
    _nearest_row_metric,
    _rolling_skew_suite,
)
from analyze.options.config import (
    SKEWNESS_RESULT_COLUMNS,
    SKEWNESS_WINDOWS,
    SKEW_TYPE_IV_SMILE,
)

# Pair-level group key for IV skew metrics (risk reversal spans CALL+PUT).
_IV_GROUP_KEY = ["underlying_code", "expiry_date"]


def _smile_skewness_by_group(
    out: pd.DataFrame, value_col: str = "iv_pct",
) -> pd.DataFrame:
    """OI-weighted 3rd standardized moment of a value column per
    (date, expiry group).

    out must have: _EXPIRY_GROUP_KEY + [date, value_col, open_interest].
    Returns smile_key + [smile_skewness]; NaN where the group has fewer
    than _SMILE_MIN_CONTRACTS valid contracts or zero value spread.
    """
    out = out.copy()
    out["w"] = out["open_interest"].clip(lower=1.0)
    out["wx"] = out["w"] * out[value_col]
    out["wxx"] = out["w"] * out[value_col] ** 2
    out["wxxx"] = out["w"] * out[value_col] ** 3

    smile_key = ["date"] + _EXPIRY_GROUP_KEY
    moments = (
        out.groupby(smile_key, as_index=False, sort=False)
        .agg(
            n=(value_col, "count"),
            w_sum=("w", "sum"),
            wx=("wx", "sum"),
            wxx=("wxx", "sum"),
            wxxx=("wxxx", "sum"),
        )
    )
    mean = moments["wx"] / moments["w_sum"]
    m2 = moments["wxx"] / moments["w_sum"] - mean**2
    m3 = (
        moments["wxxx"] / moments["w_sum"]
        - 3.0 * mean * (moments["wxx"] / moments["w_sum"])
        + 2.0 * mean**3
    )
    std = np.sqrt(m2.clip(lower=0.0))
    with np.errstate(divide="ignore", invalid="ignore"):
        skew = np.where(
            (moments["n"] >= _SMILE_MIN_CONTRACTS) & (std > 1e-8),
            m3 / std**3,
            np.nan,
        )
    moments["smile_skewness"] = skew
    return moments[smile_key + ["smile_skewness"]]


def compute_options_iv_smile_corr_stats(df: pd.DataFrame) -> pd.DataFrame:
    """Compute rolling stats of the IV smile skewness (skew_type='iv_smile').

    Companion to compute_options_iv_skew_stats: takes the same per-contract
    input frame and runs the shared rolling skewness suite
    (_rolling_skew_suite) on the OI-weighted 3rd standardized moment of IV
    (smile_skewness). Correlations use the S * smile_skew price basis
    (same formula as the oi_moneyness rows, so the two data sources'
    correlations are comparable). The 1%-per-unit display rebase
    (S * (1 + (skew-1)/100), skew=1 sits exactly on the spot curve) is
    applied in the frontend chart only.

    Args:
        df: DataFrame from fetch.fetch_iv_skew_rows (same input as
            compute_options_iv_skew_stats).

    Returns:
        DataFrame with SKEWNESS_RESULT_COLUMNS for
        skew_type='iv_smile' — written to analysis.options_skewness_stats.
    """
    if df.empty:
        return pd.DataFrame(columns=SKEWNESS_RESULT_COLUMNS)

    out = _collapse_open_expiry_rows(df)
    out["iv_pct"] = out["implied_vol"] * 100.0

    smile = _smile_skewness_by_group(out)
    smile = smile.dropna(subset=["smile_skewness"]).rename(
        columns={"smile_skewness": "skewness"}
    )
    if smile.empty:
        return pd.DataFrame(columns=SKEWNESS_RESULT_COLUMNS)

    group_key = ["date"] + _EXPIRY_GROUP_KEY
    spot = (
        out.groupby(group_key, as_index=False, sort=False)
        .agg(underlying_close=("underlying_close", "first"))
    )
    agg = smile.merge(spot, on=group_key, how="left")
    agg["skew_type"] = SKEW_TYPE_IV_SMILE
    agg = _rolling_skew_suite(agg)
    return _finalize_skew_result(agg)


def compute_options_iv_skew_stats(df: pd.DataFrame) -> pd.DataFrame:
    """Compute per-expiry-group implied-volatility skew stats.

    Premium information enters via implied_vol (calibrated from settle
    prices with Black-76 and stored in stats.options_greeks). All output
    IV/skew values are in vol points (percent).

    Daily metrics per (date, underlying_code, expiry_date):
      atm_iv          IV of the contract with moneyness closest to 1.0
      iv_call25       IV of the OTM CALL nearest |delta| = 0.25
      iv_put25        IV of the OTM PUT nearest |delta| = 0.25
      risk_reversal_25d = iv_call25 - iv_put25
                          (negative = puts richer = downside hedging demand)
      put_skew_25d    = iv_put25 - atm_iv
      call_skew_25d   = iv_call25 - atm_iv

    Per (date, option_type, underlying_code, expiry_date):
      smile_skewness  OI-weighted 3rd standardized moment of IV across
                      strikes (>= 3 valid contracts required)

    Rolling suite on risk_reversal_25d per (underlying_code, expiry_date):
      rr25_maW / rr25_stdW (5/20/60), full-history slopes of rr25 and its
      MAs, and expanding correlation of rr25_maW vs spot MA.

    Open (non-matured) expiry groups are collapsed to the mean expiry date
    per (option_type, underlying_code), matching the expiry_identity FK.

    Args:
        df: DataFrame from fetch.fetch_iv_skew_rows with columns:
            date, contract_code, option_type, underlying_code, expiry_date,
            strike_price, underlying_close, open_interest, implied_vol, delta.

    Returns:
        DataFrame with IV_SKEW_RESULT_COLUMNS (risk-reversal metrics
        duplicated for CALL/PUT rows, like the oi_stats convention).
    """
    from analyze.options.config import IV_SKEW_RESULT_COLUMNS

    if df.empty:
        return pd.DataFrame(columns=IV_SKEW_RESULT_COLUMNS)

    out = _collapse_open_expiry_rows(df)

    # ---- Step 1: contract-level derived columns -------------------------
    out["iv_pct"] = out["implied_vol"] * 100.0
    out["moneyness"] = out["strike_price"] / out["underlying_close"]
    out["dist_atm"] = (out["moneyness"] - 1.0).abs()

    otm_call = out[
        (out["option_type"] == "CALL")
        & (out["delta"] > 0) & (out["delta"] < _DELTA_OTM_MAX)
    ].copy()
    otm_call["dist_25d"] = (otm_call["delta"] - _DELTA_TARGET).abs()

    otm_put = out[
        (out["option_type"] == "PUT")
        & (out["delta"] > -_DELTA_OTM_MAX) & (out["delta"] < 0)
    ].copy()
    otm_put["dist_25d"] = (otm_put["delta"] + _DELTA_TARGET).abs()

    pair_key = ["date"] + _IV_GROUP_KEY

    # ---- Step 2: pair-level daily metrics (ATM + 25d wings) -------------
    atm = _nearest_row_metric(
        out, "dist_atm", "iv_pct", pair_key, "atm_iv",
    )
    call25 = _nearest_row_metric(
        otm_call, "dist_25d", "iv_pct", pair_key, "iv_call25",
    )
    put25 = _nearest_row_metric(
        otm_put, "dist_25d", "iv_pct", pair_key, "iv_put25",
    )

    spot = (
        out.groupby(pair_key, as_index=False, sort=False)
        .agg(underlying_close=("underlying_close", "first"))
    )

    daily = spot.merge(atm, on=pair_key, how="left")
    daily = daily.merge(call25, on=pair_key, how="left")
    daily = daily.merge(put25, on=pair_key, how="left")

    daily["risk_reversal_25d"] = daily["iv_call25"] - daily["iv_put25"]
    daily["put_skew_25d"] = daily["iv_put25"] - daily["atm_iv"]
    daily["call_skew_25d"] = daily["iv_call25"] - daily["atm_iv"]

    if daily.empty:
        return pd.DataFrame(columns=IV_SKEW_RESULT_COLUMNS)

    # ---- Step 3: rolling suite on risk_reversal_25d ---------------------
    daily = daily.sort_values(_IV_GROUP_KEY + ["date"]).reset_index(drop=True)

    for w in SKEWNESS_WINDOWS:
        daily[f"rr25_ma{w}"] = grouped_rolling_agg(
            daily, _IV_GROUP_KEY, "risk_reversal_25d",
            window=w, min_periods=w, agg="mean",
        )
    for w in SKEWNESS_WINDOWS:
        daily[f"rr25_std{w}"] = grouped_rolling_agg(
            daily, _IV_GROUP_KEY, "risk_reversal_25d",
            window=w, min_periods=w, agg="std", ddof=1,
        )

    daily["_t"] = daily.groupby(_IV_GROUP_KEY, sort=False).cumcount()
    daily = _broadcast_slopes(
        daily, "risk_reversal_25d", "rr25_slope", _IV_GROUP_KEY,
    )
    for w in SKEWNESS_WINDOWS:
        daily = _broadcast_slopes(
            daily, f"rr25_ma{w}", f"rr25_ma{w}_slope", _IV_GROUP_KEY,
        )

    for w in SKEWNESS_WINDOWS:
        daily[f"spot_ma{w}"] = grouped_rolling_agg(
            daily, _IV_GROUP_KEY, "underlying_close",
            window=w, min_periods=w, agg="mean",
        )
        daily[f"corr_rr25_ma{w}_vs_spot_ma{w}"] = _expanding_corr(
            daily, _IV_GROUP_KEY,
            f"rr25_ma{w}", f"spot_ma{w}",
            min_periods=w,
        ).values

    daily = daily.drop(
        columns=["underlying_close"]
        + [f"spot_ma{w}" for w in SKEWNESS_WINDOWS]
        + ["_t"]
    )

    # ---- Step 4: duplicate pair metrics for CALL/PUT rows ---------------
    call_rows = daily.copy()
    call_rows["option_type"] = "CALL"
    put_rows = daily.copy()
    put_rows["option_type"] = "PUT"
    result = pd.concat([call_rows, put_rows], ignore_index=True)

    # ---- Step 5: smile skewness (OI-weighted 3rd moment of IV) ----------
    smile_key = ["date"] + _EXPIRY_GROUP_KEY
    smile = _smile_skewness_by_group(out)

    # ---- Step 6: merge smile + select result columns --------------------
    result = result.merge(smile, on=smile_key, how="left")
    result = result[IV_SKEW_RESULT_COLUMNS].copy()
    result = result.sort_values(
        ["date", "option_type", "underlying_code", "expiry_date"]
    ).reset_index(drop=True)
    return result
