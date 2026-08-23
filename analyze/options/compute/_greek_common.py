"""Shared machinery for the per-greek skew metrics (greek_* skew types).

Each greek_* skew metric is a PAIR-level (date, underlying_code,
expiry_date) CALL-vs-PUT contrast — the open-interest mirror of the IV
risk-reversal construction in iv_skew.py — implemented by a dedicated
module (greek_delta / greek_gamma / greek_vega). Greeks WITHOUT an
industry-standard positioning skew (theta ≈ −½σ²S²Γ/365 is collinear
with gamma by construction; rho ∝ T is negligible for short-dated
options) are deliberately NOT computed.

Common conventions (per pair group, per day):
- weights w = open_interest with zero OI = zero vote (no clip): an
untraded strike holds no market expectation;
- groups with no OI on the relevant contracts (whole chain for
delta/gamma, OTM wings for vega) keep their row with
skewness = NaN (NULL in the DB) — an honest "no positioning signal
that day". This preserves the incremental-detection contract
("every valid source group has a row" — fetch_missing_iv_skew_groups
checks PK presence, not values), so runs without --force settle
quietly instead of re-detecting the same zero-OI groups forever. The
frontend series/corr/cross-count queries all filter
`skewness IS NOT NULL`, so NULL rows are invisible downstream;
- the pair-level value is duplicated onto CALL and PUT rows (the
table PK includes option_type; same convention as
compute_options_oi_stats / compute_options_iv_skew_stats), so the
frontend's AVG over option_type is a no-op.
"""
from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pandas as pd

from analyze.options.compute._shared import (
    _DELTA_OTM_MAX,
    _collapse_open_expiry_rows,
    _finalize_skew_result,
    _rolling_skew_suite,
)

# Pair-level group key (CALL+PUT span the metric; option_type is display-only).
_PAIR_GREEK_KEY = ["date", "underlying_code", "expiry_date"]


def compute_pair_greek_balance(
    df: pd.DataFrame,
    greek: str,
    *,
    skew_type: str,
    neutral: float,
    price_k: float,
    otm_wings_only: bool,
    metric: Callable[[pd.Series, pd.Series], pd.Series],
) -> pd.DataFrame:
    """Compute one pair-level greek balance metric + the rolling suite.

    Per (date, underlying_code, expiry_date) pair group, sums the
    OI-weighted greek amounts on the CALL and PUT sides and applies the
    per-greek ``metric`` callable to the two sums.

    Args:
        df: contract rows from fetch.fetch_iv_skew_rows (carries all
            greek columns + open_interest + delta + underlying_close).
        greek: greek column to weight by ('delta'/'gamma'/'vega').
        skew_type: skew_type label ('greek_<name>').
        neutral: no-tilt anchor of the metric (gap = skewness - neutral;
            anchors the cross counts, gap columns and the price rebase).
        price_k: price-space rebase scale for the correlation basis
            (skew_price = S * (1 + (skewness - neutral) * price_k)).
        otm_wings_only: restrict to OTM wings (calls 0 < delta < 0.5,
            puts -0.5 < delta < 0 — the same band as the iv_call25 /
            iv_put25 pickers); False = whole chain (PCR / GEX convention).
        metric: callable(call_sum, put_sum) -> values; receives the
            per-pair OI*greek-weighted CALL and PUT sums (pd.Series) and
            returns the daily metric (undefined rows are masked to NaN).

    Returns:
        DataFrame with SKEWNESS_RESULT_COLUMNS for the given skew_type —
        written to analysis.options_skewness_stats.
    """
    from analyze.options.config import SKEWNESS_RESULT_COLUMNS

    if df.empty or greek not in df.columns:
        return pd.DataFrame(columns=SKEWNESS_RESULT_COLUMNS)

    out = _collapse_open_expiry_rows(df)
    out = out[np.isfinite(out[greek])]
    if otm_wings_only:
        band = (
            ((out["option_type"] == "CALL")
             & (out["delta"] > 0) & (out["delta"] < _DELTA_OTM_MAX))
            | ((out["option_type"] == "PUT")
               & (out["delta"] > -_DELTA_OTM_MAX) & (out["delta"] < 0))
        )
        out = out[band]
    if out.empty:
        return pd.DataFrame(columns=SKEWNESS_RESULT_COLUMNS)

    # OI weights: zero OI = zero vote. Expectation metrics must not let
    # untraded strikes participate (no clip(lower=1) here).
    w = out["open_interest"].fillna(0.0).clip(lower=0.0)
    amt = w * out[greek].abs()

    is_call = out["option_type"] == "CALL"
    out = out.assign(
        _amt_call=np.where(is_call, amt, 0.0),
        _amt_put=np.where(~is_call, amt, 0.0),
    )

    agg = (
        out.groupby(_PAIR_GREEK_KEY, as_index=False, sort=False)
        .agg(
            call_sum=("_amt_call", "sum"),
            put_sum=("_amt_put", "sum"),
            underlying_close=("underlying_close", "first"),
        )
    )

    total = agg["call_sum"] + agg["put_sum"]
    values = metric(agg["call_sum"], agg["put_sum"])
    # Zero-OI groups keep their row with skewness = NaN (NULL in the DB):
    # the incremental missing-PK detection checks row presence, not
    # values — dropping them would re-detect the same groups forever
    # (see module docstring).
    agg["skewness"] = np.where(total > 0, values, np.nan)
    agg = agg.drop(columns=["call_sum", "put_sum"])
    if agg.empty:
        return pd.DataFrame(columns=SKEWNESS_RESULT_COLUMNS)

    # Duplicate the pair-level metric onto CALL and PUT rows (the PK
    # includes option_type; pair-metric convention, see module docstring).
    call_rows = agg.copy()
    call_rows["option_type"] = "CALL"
    put_rows = agg.copy()
    put_rows["option_type"] = "PUT"
    agg = pd.concat([call_rows, put_rows], ignore_index=True)

    agg["skew_type"] = skew_type
    agg = _rolling_skew_suite(agg, neutral=neutral, price_k=price_k)
    return _finalize_skew_result(agg)
