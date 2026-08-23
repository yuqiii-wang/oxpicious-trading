"""Gamma skew metric — normalized GEX-style call-vs-put gamma balance
(skew_type = 'greek_gamma').

Industry anchor: Gamma Exposure (GEX) — the dealers' standard gamma
positioning metric — signs call gamma positive and put gamma negative
(the dealer-positioning convention). Black-76 gamma is identical for
the call and put at the same strike, so the balance reduces to the
gamma-weighted call-minus-put OI net of the chain.

Per (date, underlying_code, expiry_date) pair group, over the WHOLE
chain (GEX convention):

    gamma_bal = (Σ_call w·Γ − Σ_put w·Γ) / (Σ_call w·Γ + Σ_put w·Γ)

with w = open_interest (zero OI = zero vote). Range [−1, 1], neutral
0 = balanced:
  > 0 — call OI dominates where gamma lives; via the dealer lens
        (customers buy puts / sell covered calls → dealers long calls,
        short puts): dealers net long gamma → volatility suppression /
        pinning
  < 0 — put OI dominates → dealers net short gamma → moves amplify

The dealer lens is an ASSUMPTION (weakest in retail-heavy A-share
flow); the stored quantity itself — the gamma-weighted call/put OI
net — is observable and assumption-free. Rows are PAIR-level: the
CALL and PUT rows of the same (date, underlying, expiry) hold the
SAME value.
"""
from __future__ import annotations

import pandas as pd

from analyze.options.compute._greek_common import compute_pair_greek_balance

_GREEK = "gamma"
_SKEW_TYPE = "greek_gamma"
_NEUTRAL = 0.0


def compute_options_greek_gamma_skew_stats(df: pd.DataFrame) -> pd.DataFrame:
    """Compute rolling stats of the GEX-style gamma balance.

    Args:
        df: DataFrame from fetch.fetch_iv_skew_rows (same input as the
            IV skew pipeline; carries the gamma column).

    Returns:
        DataFrame with SKEWNESS_RESULT_COLUMNS for skew_type='greek_gamma'
        — written to analysis.options_skewness_stats.
    """
    from analyze.options.config import GREEK_SKEW_PRICE_K

    return compute_pair_greek_balance(
        df,
        _GREEK,
        skew_type=_SKEW_TYPE,
        neutral=_NEUTRAL,
        price_k=GREEK_SKEW_PRICE_K,
        otm_wings_only=False,
        metric=lambda call_sum, put_sum: (call_sum - put_sum)
        / (call_sum + put_sum),
    )
