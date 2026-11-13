"""study_kit.studies.ema — exponential moving average (one calc, one file)."""
from __future__ import annotations

import pandas as pd


def ema(s: pd.Series, span: int) -> pd.Series:
    """Exponential moving average (pandas convention, adjust=False).

    ``fetch_daily_bars`` already carries ema12/ema26 precomputed.
    """
    return s.ewm(span=span, adjust=False).mean()
