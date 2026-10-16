"""donchian — rolling-extreme channel bounds.

The classic Donchian channel / breakout-system levels: the roof is the
MAXIMUM intraday high and the floor the MINIMUM intraday low of the
window [max(0, i-w+1), i] — the hard channel edge the whole window's
price stayed inside. Whole-frame grouped rolling max/min (GPU under
cudf.pandas); a window with a single valid bar already has levels
(min_periods=1), mirroring the anchor table's partial-window rule.
"""
from __future__ import annotations

import pandas as pd

from _common.df_utils import grouped_rolling_agg


def attach_donchian_estimates(
    df: pd.DataFrame,
    group_keys: list[str],
    windows,
) -> None:
    """Assign ``roof_donchian_{w}d`` / ``floor_donchian_{w}d`` per window."""
    for w in windows:
        df[f"roof_donchian_{w}d"] = grouped_rolling_agg(
            df, group_keys, "high", w,
            min_periods=1, agg="max", sort=False,
        )
        df[f"floor_donchian_{w}d"] = grouped_rolling_agg(
            df, group_keys, "low", w,
            min_periods=1, agg="min", sort=False,
        )
