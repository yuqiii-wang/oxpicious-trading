"""study_kit.studies.kdj — the classic KDJ stochastic (one calc, one file)."""
from __future__ import annotations

import pandas as pd


def kdj(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    n: int = 9,
    smooth: float = 3.0,
) -> pd.DataFrame:
    """The classic KDJ stochastic over the trailing ``n``-bar range:
    RSV = (close − low_n) / (high_n − low_n) × 100, K = RSV's EMA
    (alpha = 1/smooth, the A-share convention), D = K's EMA(same),
    J = 3K − 2D, as a {k, d, j} DataFrame.

    Zones: K/D > 80 the high band (超买-adjacent — exhaustion context,
    not a bare signal), < 20 the low band (超卖-adjacent); a K/D
    crossing IS the 金叉/死叉 event (k crosses above d = golden);
    J extremes (> 100 / < 0) mark the stretched tails. NOT
    materialized anywhere — always computed.
    """
    low_n = low.rolling(n, min_periods=n).min()
    high_n = high.rolling(n, min_periods=n).max()
    rsv = (close - low_n) / (high_n - low_n) * 100.0
    k = rsv.ewm(alpha=1.0 / smooth, adjust=False, min_periods=1).mean()
    d = k.ewm(alpha=1.0 / smooth, adjust=False, min_periods=1).mean()
    j = 3.0 * k - 2.0 * d
    return pd.DataFrame({"k": k, "d": d, "j": j})
