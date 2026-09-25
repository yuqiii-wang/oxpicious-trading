"""Margin-buy intensity features (analyze.analysis_forecasts.fetch
.margin).

The margin_ratio family's per-day state inputs, derived on the long
cudf.pandas frame (grouped rolling moments + a 1-row shift — no
look-ahead).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from _common.df_utils import grouped_rolling_agg, grouped_shift

from analyze.analysis_forecasts.config import (
    MARGIN_RATIO_Z_MIN_PERIODS,
    MARGIN_RATIO_Z_WINDOW,
)


def add_margin_ratio_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add the margin_ratio family's per-day state inputs (no look-ahead).

    Columns added:
        ratio — the daily 融资买入额/成交额 intensity ratio
                rz_buy / trading_amount, defined only on margin-buy days
                (rz_buy > 0, trading_amount > 0); NULL otherwise (a
                NULL rz_buy — index codes have no margin row — is also
                NULL ratio).
        nb    — no-margin-buy flag: rz_buy == 0 with trading_amount > 0
                (margin traders absent that day; the margin_ratio
                family's "inactive" state). rz_buy NULL → False.
        ratio_z — z = (ratio - μ) / σ with μ/σ = the code's rolling
                MARGIN_RATIO_Z_WINDOW-row sample moments of ratio
                (min_periods MARGIN_RATIO_Z_MIN_PERIODS non-NULL
                observations), SHIFTED 1 row (yesterday's moments are
                today's bars — no look-ahead). NULL where ratio is
                NULL or the history is short.

    The caller must have sorted df by (code, date) (the SQL ORDER BY).
    All ops are grouped pandas (cudf.pandas-accelerated). Conditions are
    kept PURE booleans: comparisons on NULL-bearing series yield
    nullable <NA> masks, and assigning/comparing with those poisons the
    cudf frame into object dtype (every later grouped op then falls
    back to slow pandas) — NULLs are filled BEFORE each compare.
    """
    ta = df["trading_amount"]
    rb = df["rz_buy"]
    ta_pos = ta.fillna(0.0) > 0          # ta.notna() & ta > 0
    rb_buy = rb.fillna(-1.0) > 0         # rb.notna() & rb > 0
    df["ratio"] = (rb / ta).where(rb_buy & ta_pos)
    df["nb"] = (rb.fillna(-1.0) == 0) & ta_pos

    mu = grouped_rolling_agg(
        df, "code", "ratio", MARGIN_RATIO_Z_WINDOW,
        min_periods=MARGIN_RATIO_Z_MIN_PERIODS, agg="mean", sort=False,
    )
    sig = grouped_rolling_agg(
        df, "code", "ratio", MARGIN_RATIO_Z_WINDOW,
        min_periods=MARGIN_RATIO_Z_MIN_PERIODS, agg="std", ddof=1,
        sort=False,
    )
    df["_mu"], df["_sig"] = mu, sig
    grouped_shift(df, "code", ["_mu", "_sig"], ["_mu_l", "_sig_l"],
                  periods=1, sort=False)
    df["ratio_z"] = ((df["ratio"] - df["_mu_l"]) / df["_sig_l"]).where(
        df["_sig_l"].fillna(0.0) > 0)

    return df.drop(columns=["_mu", "_sig", "_mu_l", "_sig_l"])
