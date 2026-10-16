"""boll — Bollinger-style band at the roof/floor window.

SMA(close, period) ± k × population σ (k = 2, ddof=0 — the same
population-σ convention as the detail table's std_{W}days columns).
The flat, slope-free counterpart of the channel method: levels the
window's own mean and volatility imply. Whole-frame grouped rolling
mean + std (GPU under cudf.pandas); NaN closes are skipped by the
rolling windows and count against min_periods (2).
"""
from __future__ import annotations

import pandas as pd

from _common.df_utils import grouped_rolling_agg
from analysis.mov_ave_spread.config.roof_floor_estimate import (
    ESTIMATE_BOLL_K,
)


def attach_bollinger_estimates(
    df: pd.DataFrame,
    group_keys: list[str],
    windows,
) -> None:
    """Assign ``roof_boll_{w}d`` / ``floor_boll_{w}d`` per window."""
    k = ESTIMATE_BOLL_K
    for w in windows:
        mean = grouped_rolling_agg(
            df, group_keys, "price", w,
            min_periods=2, agg="mean", sort=False,
        )
        std = grouped_rolling_agg(
            df, group_keys, "price", w,
            min_periods=2, agg="std", ddof=0, sort=False,
        )
        df[f"roof_boll_{w}d"] = mean + k * std
        df[f"floor_boll_{w}d"] = mean - k * std
