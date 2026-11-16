"""analysis.market_crowdedness.compute.finalize — shared gates + blend.

The defined-row gate and the percentile-normalization / score-blend /
storage-rounding finalize pass, shared verbatim by the rolling path and
every year-pinned variant (the percentile history is the frame's own
(pool, date) rows, so a one-year frame reads the variant's own history).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from _common.df_utils import grouped_rolling_agg

from analysis.market_crowdedness.compute.metrics import (
    _clip01,
    _rolling_pillar_pcts,
)
from analysis.market_crowdedness.config import (
    MIN_ACTIVE_POOL,
    SCORE_MA_WINDOW,
    TOP_CUTS,
    W_COMOVEMENT,
    W_CONCENTRATION,
    W_FLOW,
    W_VALUATION,
)


def _gate_defined_rows(agg: pd.DataFrame) -> pd.DataFrame:
    """Defined-row gate shared by the rolling and pinned paths: active
    pool >= MIN_ACTIVE_POOL, a non-empty hot set, and defined turnover
    pillars / effective-N — the later pillars only join rows that
    survive here."""
    return agg[
        (agg["active_n"] >= MIN_ACTIVE_POOL)
        & (agg["hot_n_defined"] > 0)
        & agg["concentration"].notna()
        & agg["flow_change"].notna()
        & agg["effective_n"].notna()
    ].reset_index(drop=True)


def _finalize_pillars(agg: pd.DataFrame) -> pd.DataFrame:
    """Percentile normalization + score blend + storage rounding over a
    per-(pool, date) pillar frame sorted (sec_type, pool_code, date) on
    entry — shared by the rolling pass and every year-pinned variant
    (the percentile history is the frame's own (pool, date) rows, so a
    one-year frame reads the variant's own history)."""
    agg = agg.sort_values(
        ["sec_type", "pool_code", "date"], kind="mergesort"
    ).reset_index(drop=True)
    agg = _rolling_pillar_pcts(
        agg,
        [("concentration", "concentration_pct"),
         ("flow_change", "flow_pct"),
         ("comovement", "comovement_pct"),
         ("valuation", "valuation_pct")])

    ok = (
        agg["active_n"].ge(MIN_ACTIVE_POOL)
        & agg["concentration"].notna()
        & agg["flow_change"].notna()
        & agg["concentration_pct"].notna()
        & agg["flow_pct"].notna()
    )
    agg = agg[ok].reset_index(drop=True)
    if agg.empty:
        return agg

    # Blend from unrounded percentiles over the DEFINED pillars (the
    # two turnover pillars are required; comovement/valuation drop out
    # with their weights); clip guards the 0..1 contract against fp
    # noise; round is the storage convention (NUMERIC(10,4)).
    pillars = [
        ("concentration_pct", W_CONCENTRATION),
        ("flow_pct", W_FLOW),
        ("comovement_pct", W_COMOVEMENT),
        ("valuation_pct", W_VALUATION),
    ]
    num = agg["concentration_pct"] * 0.0
    den = agg["concentration_pct"] * 0.0
    for col, w in pillars:
        v = agg[col]
        num = num + v.fillna(0.0) * w
        den = den + v.notna().astype("float64") * w
    score = num / den.where(den > 0.0)

    agg["concentration"] = np.round(_clip01(agg["concentration"]), 4)
    agg["flow_change"] = np.round(_clip01(agg["flow_change"]), 4)
    agg["comovement"] = np.round(agg["comovement"], 4)
    agg["valuation"] = np.round(_clip01(agg["valuation"]), 4)
    for _frac, col in TOP_CUTS:
        agg[col] = np.round(_clip01(agg[col]), 4)
    # effective_n is a COUNT (1..active_n) — no clip.
    agg["effective_n"] = np.round(agg["effective_n"], 4)
    for col, _w in pillars:
        agg[col] = np.round(_clip01(agg[col]), 4)
    agg["crowdedness_score"] = np.round(_clip01(score), 4)

    # ONLINE smoothing layer: the SCORE_MA_WINDOW pool-date-row moving
    # average per pool — over the ROUNDED stored score (the stored ma5
    # stays verifiable in SQL as AVG of the trailing 5 scores). The
    # frame is sorted (sec_type, pool_code, date) on entry, so the
    # grouped rolling sees correct temporal order; the same call inside
    # a year-variant frame rolls within the variant's own series.
    agg["crowdedness_score_ma5"] = np.round(grouped_rolling_agg(
        agg, ["sec_type", "pool_code"], "crowdedness_score",
        SCORE_MA_WINDOW, min_periods=1, agg="mean", sort=False,
    ), 4)
    return agg
