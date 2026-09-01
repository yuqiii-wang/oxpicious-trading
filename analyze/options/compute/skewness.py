"""oi_moneyness skew data source — OI-weighted mean moneyness per expiry
group (skew_type = 'oi_moneyness', a POSITIONING metric).

Persists per-expiry-group rolling stats:
  skewness_ma5/20/60  — rolling MA of moneyness over 5/20/60 days
  skewness_std5/20/60 — rolling STD of moneyness over 5/20/60 days
  gap_skewness_vs_spot_maW = skewness_maW - 1
  gap_skewness_vs_spot_slope — full-history slope of (moneyness - 1)
  gap_skewness_vs_spot_maW_slope — full-history slope of gap_maW
  corr_skewness_vs_spot — 60-day rolling corr(moneyness, spot)
  count_skewness_curve_crossed_spot — cumulative count of sign changes
    in (skewness - 1) for this expiry group
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from analyze.options.compute._shared import (
    _EXPIRY_GROUP_KEY,
    _apply_open_expiry_collapse,
    _finalize_skew_result,
    _rolling_skew_suite,
)
from analyze.options.config import SKEWNESS_RESULT_COLUMNS, SKEW_TYPE_MONEYNESS


def compute_options_skewness_stats(df: pd.DataFrame) -> pd.DataFrame:
    """Compute per-expiry-group rolling OI-moneyness skewness stats
    (skew_type = 'oi_moneyness').

    Args:
        df: DataFrame from fetch.fetch_options_skewness_rows with columns:
            date, contract_code, option_type, underlying_code, expiry_date,
            strike_price, underlying_close, open_interest.

    Returns:
        DataFrame with SKEWNESS_RESULT_COLUMNS — one row per
        (date, option_type, underlying_code, expiry_date, skew_type),
        with rolling MA/STD/gap/slope/correlation stats of OI-weighted
        mean moneyness. For open (non-matured) expiry groups, expiry_date
        is collapsed to the mean of all expiry dates per
        (option_type, underlying_code).
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
    # Vectorized shared helper (merge + where). Re-aggregate only when
    # open rows existed so collapsed groups are properly summed.
    dataset_max_date = agg["date"].max()
    has_open = bool((agg["expiry_date"] > dataset_max_date).any())
    agg = _apply_open_expiry_collapse(agg, dataset_max_date)
    if has_open:
        agg = (
            agg.groupby(["date"] + _EXPIRY_GROUP_KEY, as_index=False, sort=False)
            .agg(
                w_sum=("w_sum", "sum"),
                wm_sum=("wm_sum", "sum"),
                underlying_close=("underlying_close", "first"),
            )
        )

    agg["skewness"] = agg["wm_sum"] / agg["w_sum"]
    agg = agg.drop(columns=["w_sum", "wm_sum"])

    if agg.empty:
        return pd.DataFrame(columns=SKEWNESS_RESULT_COLUMNS)

    agg["skew_type"] = SKEW_TYPE_MONEYNESS
    agg = _rolling_skew_suite(agg)
    return _finalize_skew_result(agg)
