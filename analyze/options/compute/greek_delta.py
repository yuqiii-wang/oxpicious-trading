"""Delta skew metric — delta-weighted put/call OI ratio (dpcr)
(skew_type = 'greek_delta').

Industry anchor: the put/call ratio (CBOE PCR, OI-based), refined by
weighting each contract's OI by |delta| — an ATM put carries twice the
directional exposure of a 25-delta put, so raw OI understates the
bearish side. The plain (unweighted) PCR already lives in
options_oi_stats (analyze/options/compute/oi_stats.py); this is its
moneyness-aware upgrade.

Per (date, underlying_code, expiry_date) pair group, over the WHOLE
chain (wings + ATM + ITM — the PCR convention):

    dpcr = Σ_put w·|Δ| / (Σ_call w·|Δ| + Σ_put w·|Δ|)

with w = open_interest (zero OI = zero vote). Range [0, 1], neutral
0.5 = balanced directional book:
  > 0.5 — put-side directional exposure dominates (bearish / hedged)
  < 0.5 — call-side dominates (bullish)

Open interest is two-sided (every contract has a long AND a short), so
this measures where DIRECTIONAL EXPOSURE is concentrated, not who is
long/short. Rows are PAIR-level: the CALL and PUT rows of the same
(date, underlying, expiry) hold the SAME value.
"""
from __future__ import annotations

import pandas as pd

from analyze.options.compute._greek_common import compute_pair_greek_balance

_GREEK = "delta"
_SKEW_TYPE = "greek_delta"
_NEUTRAL = 0.5


def compute_options_greek_delta_skew_stats(df: pd.DataFrame) -> pd.DataFrame:
    """Compute rolling stats of the delta-weighted put/call OI ratio.

    Args:
        df: DataFrame from fetch.fetch_iv_skew_rows (same input as the
            IV skew pipeline; carries the delta column).

    Returns:
        DataFrame with SKEWNESS_RESULT_COLUMNS for skew_type='greek_delta'
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
        metric=lambda call_sum, put_sum: put_sum / (call_sum + put_sum),
    )
