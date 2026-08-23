"""Vega skew metric — OTM-wing vega balance (vol-demand direction)
(skew_type = 'greek_vega').

Industry anchor: the 25-delta risk reversal (risk_reversal_25d =
iv_call25 − iv_put25 in iv_skew.py) on the PRICING side; this is its
OPEN-INTEREST mirror — which wing the market holds vol exposure in.

Per (date, underlying_code, expiry_date) pair group, OTM wings only
(calls 0 < Δ < 0.5, puts −0.5 < Δ < 0 — the same band as the
iv_call25 / iv_put25 pickers):

    vega_bal = (Σ_C w·ν − Σ_P w·ν) / (Σ_C w·ν + Σ_P w·ν)

with w = open_interest (zero OI = zero vote). Range [−1, 1], neutral
0 = balanced vol demand:
  > 0 — vol demand concentrated in upside calls (upside-event bets)
  < 0 — downside puts (crash hedging)

ATM contracts are excluded by design: straddle-style ATM vol demand is
direction-neutral and would dilute the wing contrast. Divergence
between this and risk_reversal_25d (positioning vs price) is itself a
signal. Rows are PAIR-level: the CALL and PUT rows of the same
(date, underlying, expiry) hold the SAME value.
"""
from __future__ import annotations

import pandas as pd

from analyze.options.compute._greek_common import compute_pair_greek_balance

_GREEK = "vega"
_SKEW_TYPE = "greek_vega"
_NEUTRAL = 0.0


def compute_options_greek_vega_skew_stats(df: pd.DataFrame) -> pd.DataFrame:
    """Compute rolling stats of the OTM-wing vega balance.

    Args:
        df: DataFrame from fetch.fetch_iv_skew_rows (same input as the
            IV skew pipeline; carries the vega column).

    Returns:
        DataFrame with SKEWNESS_RESULT_COLUMNS for skew_type='greek_vega'
        — written to analysis.options_skewness_stats.
    """
    from analyze.options.config import GREEK_SKEW_PRICE_K

    return compute_pair_greek_balance(
        df,
        _GREEK,
        skew_type=_SKEW_TYPE,
        neutral=_NEUTRAL,
        price_k=GREEK_SKEW_PRICE_K,
        otm_wings_only=True,
        metric=lambda call_sum, put_sum: (call_sum - put_sum)
        / (call_sum + put_sum),
    )
