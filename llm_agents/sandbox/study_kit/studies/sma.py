"""study_kit.studies.sma — simple moving average (one calc, one file)."""
from __future__ import annotations

import pandas as pd


def sma(s: pd.Series, w: int) -> pd.Series:
    """Simple moving average over window ``w`` (min_periods = w).

    Before recomputing, check whether a precomputed column already
    covers the window: ``study_kit.db.fetch_daily_bars`` returns
    ma5/20/60/120/252 straight from the views.
    """
    return s.rolling(w).mean()
