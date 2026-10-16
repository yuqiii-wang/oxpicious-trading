"""quantile — rolling close-quantile band.

A distributional envelope: the roof is the window's 90th percentile and
the floor its 10th percentile of valid closes — the levels price spent
the window's upper / lower deciles around, robust to single-bar wicks
(unlike donchian) and slope-free (unlike channel). Uses the shared
``grouped_rolling_quantile`` helper — cuDF lacks rolling quantile, so
under cudf.pandas this is the accepted one-CPU-pass-per-call convention
(the high_low_pct / pct_bands precedent); NaN closes are skipped by the
rolling window and count against min_periods (2).
"""
from __future__ import annotations

import pandas as pd

from _common.df_utils import grouped_rolling_quantile
from analysis.mov_ave_spread.config.roof_floor_estimate import (
    ESTIMATE_QUANTILE_HI,
    ESTIMATE_QUANTILE_LO,
)


def attach_quantile_estimates(
    df: pd.DataFrame,
    group_keys: list[str],
    windows,
) -> None:
    """Assign ``roof_quantile_{w}d`` / ``floor_quantile_{w}d`` per window."""
    for w in windows:
        df[f"roof_quantile_{w}d"] = grouped_rolling_quantile(
            df, group_keys, "price", w, 2, ESTIMATE_QUANTILE_HI,
        )
        df[f"floor_quantile_{w}d"] = grouped_rolling_quantile(
            df, group_keys, "price", w, 2, ESTIMATE_QUANTILE_LO,
        )
