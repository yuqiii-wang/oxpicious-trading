"""trendline — the repo's structural roof/floor estimate.

The roof/floor anchor LINE of mov_ave_spreads_roof_and_floor_lines
(two-anchor half-split semantics: 1st anchor = max/min valid CLOSE of
the window's 1st half, 2nd anchor = max/min valid CLOSE searched from
``gap`` trading days after the 1st, value = the anchor date's INTRADAY
high/low — see _common.ohlc_vector) EXTRAPOLATED to the row date.

Both anchors lie on the line, so extrapolating from the LATER one is
the same line as extrapolating from the earlier one:

  roof(date)  = high_2nd + high_line_slope * days_since_last_high
  floor(date) = low_2nd  + low_line_slope  * days_since_last_low

(days_since_last_* is the trading-day distance to the side's LAST
anchor — exactly the 2nd anchor whenever it exists, and the line's
slope is NULL whenever the 2nd anchor is absent, so the product is NaN
in exactly the no-line cases). This is the SAME value the Ma-Spread
UI's trendline segment reaches at its stop date (envelope.ts
trendlineSegment), derived here from the identical anchor computation.

Compute layout: reuses the ohlc step's sanctioned host core
(compute_group_anchors_all_windows — one sparse-table call per group
covering ALL windows) with the same B-A3 batched write (one REAL
concat of per-group frames + one .loc write per output column); the
per-window estimate arithmetic runs on the REAL per-group frames
(host pandas — inside the boundary). The anchor intermediate columns
never land on the proxied step frame.
"""
from __future__ import annotations

import pandas as pd

from _common.df_utils import host_array, safe_columns
from analysis.mov_ave_spread._common.ohlc_vector import (
    compute_group_anchors_all_windows,
)

GROUP_KEYS: list[str] = ["sec_type", "code"]


def attach_trendline_estimates(df: pd.DataFrame, windows) -> None:
    """Assign ``roof_trendline_{w}d`` / ``floor_trendline_{w}d`` per window.

    Args:
        df: step frame sorted by [sec_type, code, date] with columns
            [sec_type, code, date, price, high, low] (the FULL per-code
            history — the anchors need up to 1275 prior rows).
        windows: rolling window sizes (trading days).
    """
    real_pd_df = getattr(pd.DataFrame, "_fsproxy_slow", pd.DataFrame)
    per_group: list[pd.DataFrame] = []
    for _key, g in df.groupby(GROUP_KEYS, sort=False):
        anchors = compute_group_anchors_all_windows(
            g[["date", "price", "high", "low"]], windows,
        )
        data: dict[str, pd.Series] = {}
        for w in windows:
            data[f"roof_trendline_{w}d"] = (
                anchors[f"high_2nd_{w}d"]
                + anchors[f"high_line_slope_{w}d"]
                * anchors[f"days_since_last_high_{w}d"]
            )
            data[f"floor_trendline_{w}d"] = (
                anchors[f"low_2nd_{w}d"]
                + anchors[f"low_line_slope_{w}d"]
                * anchors[f"days_since_last_low_{w}d"]
            )
        per_group.append(real_pd_df(data, index=host_array(anchors.index)))
    if not per_group:
        return
    # REAL concat (the per-group frames are REAL pandas frames — the
    # proxied pd.concat would reject them; B-A3 batched-write pattern).
    real_concat = getattr(pd.concat, "_fsproxy_slow", pd.concat)
    merged = real_concat(per_group)
    idx = merged.index
    for col in safe_columns(merged):
        df.loc[idx, col] = host_array(merged[col].to_numpy())
