"""study_kit.studies.bollinger — the Bollinger band read (one file)."""
from __future__ import annotations

import pandas as pd

from study_kit.studies.sma import sma


def bollinger(
    close: pd.Series,
    n: int = 20,
    k: float = 2.0,
) -> pd.DataFrame:
    """The Bollinger band read over ``n`` bars at ``k`` population σ —
    {mid, upper, lower, pct_b, bandwidth}: mid = SMA(n), the bands at
    mid ± k×σ (ddof=0, the house convention — the same std the
    materialized std_*days columns store), pct_b = where the close sits
    inside the band ((close − lower) / (upper − lower); > 1 above the
    upper band, < 0 below the lower), bandwidth = (upper − lower) / mid
    — its own-history percentile (``trailing_pct_rank``) answers
    squeeze (收窄, coiled, 临近变盘) vs expansion (张开, trend
    continuation).

    The σ columns for the house windows are already materialized on
    analysis.mov_ave_spreads_detail (std_5/20/60/120/252days) — reuse
    them via study_kit.db.query when they cover the ask.
    """
    mid = sma(close, n)
    sd = close.rolling(n, min_periods=n).std(ddof=0)
    upper = mid + k * sd
    lower = mid - k * sd
    band = upper - lower
    pct_b = (close - lower) / band
    return pd.DataFrame({
        "mid": mid, "upper": upper, "lower": lower,
        "pct_b": pct_b,
        "bandwidth": band / mid,
    })
