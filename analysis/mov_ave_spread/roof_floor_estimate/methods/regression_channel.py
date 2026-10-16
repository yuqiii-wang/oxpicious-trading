"""channel — rolling linear-regression channel (Raff-style).

The OLS fit of valid closes on trading-day position over the window,
EVALUATED AT the row date, ± k × residual σ (k = 2). Unlike boll (a
flat band around the window mean) the channel slopes with the window's
trend, so its roof/floor are the trend-following levels regression-
channel traders use.

Whole-frame formulation via rolling-sum identities (NO per-window fit
loop): with p = the row's group position, y = close (NaN → 0 with a
validity mask m so every masked product is NaN-free), the six grouped
rolling sums over the window

    n = Σm   Sy = Σy   Syy = Σy²   Sp = Σp·m   Spp = Σp²·m   Spy = Σp·y

give slope / fitted value at the row position p_row / residual σ by
least-squares algebra:

    x̄ = Sp/n   ȳ = Sy/n
    b     = (n·Spy − Sp·Sy) / (n·Spp − Sp²)
    ŷ(row)= ȳ + b · (p_row − x̄)
    σ     = sqrt(max(ȳ2 − ȳ² − b²·(Spp/n − x̄²), 0))   with ȳ2 = Syy/n

All NaN handling is CONSTRUCTIVE (fillna(0) + mask products), so the
rolling sums never see a NaN and min_periods=1 suffices; the estimate
is nulled only by the final validity gate (n >= ESTIMATE_CHANNEL_MIN_
OBS, |denominator| > 1e-9). Rolling sums, products and where-chains are
cuDF-native — the whole method is one GPU pass per window.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from _common.df_utils import grouped_rolling_agg
from analysis.mov_ave_spread.config.roof_floor_estimate import (
    ESTIMATE_CHANNEL_K,
    ESTIMATE_CHANNEL_MIN_OBS,
)

# Scratch helper columns (dropped by compute.py after the method runs).
POS_COL = "_est_ch_pos"
MASK_COL = "_est_ch_m"
Y_COL = "_est_ch_y"
Y2_COL = "_est_ch_y2"
PM_COL = "_est_ch_pm"
P2M_COL = "_est_ch_p2m"
PYM_COL = "_est_ch_pym"

DENOM_EPS = 1e-9


def ensure_channel_helpers(df: pd.DataFrame, group_keys: list[str]) -> None:
    """Build the NaN-masked rolling-sum product columns (one frame pass).

    ``_est_ch_pos`` is the row's 0-based position within its group
    (grouped cumcount — the OLS x); every other helper is the y-side or
    position-side product masked by close validity so the rolling sums
    stay NaN-free.
    """
    pos = df.groupby(group_keys, sort=False).cumcount().astype("int64")
    df[POS_COL] = pos.astype("float64")
    mask = df["price"].notna().astype("float64")
    df[MASK_COL] = mask
    y0 = df["price"].fillna(0.0)
    df[Y_COL] = y0
    df[Y2_COL] = y0 * y0
    df[PM_COL] = df[POS_COL] * mask
    df[P2M_COL] = df[POS_COL] * df[POS_COL] * mask
    df[PYM_COL] = df[POS_COL] * y0


def drop_channel_helpers(df: pd.DataFrame) -> None:
    """Drop the scratch helper columns."""
    for c in (POS_COL, MASK_COL, Y_COL, Y2_COL, PM_COL, P2M_COL, PYM_COL):
        del df[c]


def attach_channel_estimates(
    df: pd.DataFrame,
    group_keys: list[str],
    windows,
) -> None:
    """Assign ``roof_channel_{w}d`` / ``floor_channel_{w}d`` per window.

    Requires ensure_channel_helpers to have run first.
    """
    k = ESTIMATE_CHANNEL_K
    for w in windows:
        s_n = grouped_rolling_agg(df, group_keys, MASK_COL, w, min_periods=1, agg="sum", sort=False)
        s_y = grouped_rolling_agg(df, group_keys, Y_COL, w, min_periods=1, agg="sum", sort=False)
        s_y2 = grouped_rolling_agg(df, group_keys, Y2_COL, w, min_periods=1, agg="sum", sort=False)
        s_p = grouped_rolling_agg(df, group_keys, PM_COL, w, min_periods=1, agg="sum", sort=False)
        s_p2 = grouped_rolling_agg(df, group_keys, P2M_COL, w, min_periods=1, agg="sum", sort=False)
        s_py = grouped_rolling_agg(df, group_keys, PYM_COL, w, min_periods=1, agg="sum", sort=False)

        mean_x = s_p / s_n
        mean_y = s_y / s_n
        denom = s_n * s_p2 - s_p * s_p
        slope = np.where(
            denom.abs() > DENOM_EPS,
            (s_n * s_py - s_p * s_y) / np.where(denom.abs() > DENOM_EPS, denom, 1.0),
            np.nan,
        )
        fit = mean_y + slope * (df[POS_COL] - mean_x)
        var_y = s_y2 / s_n - mean_y * mean_y
        var_x = s_p2 / s_n - mean_x * mean_x
        sigma = np.sqrt(np.maximum(var_y - slope * slope * var_x, 0.0))
        ok = (s_n >= ESTIMATE_CHANNEL_MIN_OBS) & denom.abs().gt(DENOM_EPS)
        # np.maximum/np.where on Series are cuDF-native (verified class);
        # the final NaN gate is a scalar-bounds mask via where.
        df[f"roof_channel_{w}d"] = np.where(ok, fit + k * sigma, np.nan)
        df[f"floor_channel_{w}d"] = np.where(ok, fit - k * sigma, np.nan)
