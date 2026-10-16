"""Wide-column orchestration for the roof/floor level estimates.

compute_estimate_columns(df) adds, to the step frame (sorted by
[sec_type, code, date] with [sec_type, code, date, price, high, low]):

  today_close
  roof_{method}_{w}d / floor_{method}_{w}d
      for every method in ESTIMATE_METHOD_ORDER and every window w in
      ESTIMATE_WINDOWS — the estimated resistance / support LEVEL at
      each row's date (float64, NaN where the method has no level).

Method order (the median needs the five base methods first):
trendline -> donchian -> quantile -> channel -> boll -> median.
"""
from __future__ import annotations

import pandas as pd

from _common.df_utils import safe_columns
from analysis.mov_ave_spread.config.roof_floor_estimate import (
    ESTIMATE_METHOD_ORDER,
    ESTIMATE_WIDE_COLUMNS,
    ESTIMATE_WINDOWS,
)
from analysis.mov_ave_spread.roof_floor_estimate.methods.bollinger_band import (
    attach_bollinger_estimates,
)
from analysis.mov_ave_spread.roof_floor_estimate.methods.donchian import (
    attach_donchian_estimates,
)
from analysis.mov_ave_spread.roof_floor_estimate.methods.median import (
    attach_median_estimates,
)
from analysis.mov_ave_spread.roof_floor_estimate.methods.quantile_band import (
    attach_quantile_estimates,
)
from analysis.mov_ave_spread.roof_floor_estimate.methods.regression_channel import (
    attach_channel_estimates,
    drop_channel_helpers,
    ensure_channel_helpers,
)
from analysis.mov_ave_spread.roof_floor_estimate.methods.trendline import (
    attach_trendline_estimates,
)

GROUP_KEYS: list[str] = ["sec_type", "code"]


def compute_estimate_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Add today_close + every method's per-window estimate columns.

    Args:
        df: step frame with columns [sec_type, code, date, price, high,
            low], the FULL per-code history (rolling methods need up to
            1275 prior rows per code).

    Returns:
        The same df with the estimate columns added in place (and the
        channel method's scratch helper columns removed).
    """
    if df.empty:
        df["today_close"] = pd.Series(dtype="float64")
        for col in ESTIMATE_WIDE_COLUMNS:
            df[col] = pd.Series(dtype="float64")
        return df

    # Ensure numeric conversion of the price columns (host-pure
    # membership — ``col in df.columns`` is a proxied fallback).
    cols = set(safe_columns(df))
    for col in ("price", "high", "low"):
        if col in cols:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    df["today_close"] = df["price"]

    # 1. trendline — the anchor-line extrapolation (host core, batched
    #    writes; anchor intermediates never touch the step frame).
    attach_trendline_estimates(df, ESTIMATE_WINDOWS)

    # 2-5. the whole-frame rolling methods (GPU under cudf.pandas; the
    #      quantile pass is the accepted CPU-fallback convention).
    attach_donchian_estimates(df, GROUP_KEYS, ESTIMATE_WINDOWS)
    attach_quantile_estimates(df, GROUP_KEYS, ESTIMATE_WINDOWS)
    ensure_channel_helpers(df, GROUP_KEYS)
    attach_channel_estimates(df, GROUP_KEYS, ESTIMATE_WINDOWS)
    drop_channel_helpers(df)
    attach_bollinger_estimates(df, GROUP_KEYS, ESTIMATE_WINDOWS)

    # 6. median — the cross-method consensus (host column-fold).
    attach_median_estimates(df, ESTIMATE_WINDOWS)

    return df
