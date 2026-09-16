"""Valuation z features (analyze.analysis_forecasts.fetch.valuation).

The pe_state / dividend_state families' per-day state inputs — one
rolling-moment z per valuation series, vs the code's OWN trailing
moments (the margin_ratio convention: a slow valuation series needs ~1y
of observations before its z is trusted; shifted 1 row — no look-ahead).
Derived on the long cudf.pandas frame with grouped rolling ops.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from _common.df_utils import grouped_rolling_agg, grouped_shift

from analyze.analysis_forecasts.config import (
    VAL_Z_MIN_PERIODS,
    VAL_Z_WINDOW,
)


def add_valuation_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add the pe_state / dividend_state families' per-day state inputs
    (no look-ahead).

    Columns added — one z per valuation series, both vs the code's OWN
    trailing moments (the margin_ratio convention; a slow valuation
    series needs ~1y of observations before its z is trusted):
        pe_z      — z = (pe[t] - μ[t-1]) / σ[t-1] with μ/σ the
                    rolling VAL_Z_WINDOW-row sample moments of
                    pe (min_periods VAL_Z_MIN_PERIODS non-NULL
                    observations) SHIFTED 1 row. NULL on invalid-PE
                    days (no-earnings / non-positive PE) — those days
                    form no PE bucket.
        div_z     — the same z on dividend_yield (all sec_types; NULL
                    for non-payers — only paying codes form dividend
                    buckets).
    NULL where the series is NULL or the history is short → the compute
    engines' NaN-compares-False masks never bucket those days.

    The caller must have sorted df by (code, date) (the SQL ORDER BY).
    All ops are grouped pandas (cudf.pandas-accelerated).
    """
    for src, out in (("pe", "pe_z"), ("dividend_yield", "div_z")):
        mu = grouped_rolling_agg(
            df, "code", src, VAL_Z_WINDOW,
            min_periods=VAL_Z_MIN_PERIODS, agg="mean", sort=False,
        )
        sig = grouped_rolling_agg(
            df, "code", src, VAL_Z_WINDOW,
            min_periods=VAL_Z_MIN_PERIODS, agg="std", ddof=1,
            sort=False,
        )
        df["_mu"], df["_sig"] = mu, sig
        grouped_shift(df, "code", ["_mu", "_sig"], ["_mu_l", "_sig_l"],
                      periods=1, sort=False)
        # Pure-boolean condition (see margin.add_margin_ratio_features):
        # NULL-bearing comparisons yield nullable <NA> masks that poison
        # the cudf frame into object dtype.
        df[out] = ((df[src] - df["_mu_l"]) / df["_sig_l"]).where(
            df["_sig_l"].fillna(0.0) > 0)
        df = df.drop(columns=["_mu", "_sig", "_mu_l", "_sig_l"])

    return df
