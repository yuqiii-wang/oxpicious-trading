"""study_kit.studies.drawdown — drawdown from a running/trailing peak."""
from __future__ import annotations

import pandas as pd


def drawdown(close: pd.Series, window: int | None = None) -> pd.Series:
    """Drawdown from the peak (≤ 0, as a fraction).

    ``window=None`` measures against the RUNNING maximum (the whole
    history so far); an int measures against the trailing w-point
    peak (``window=252`` ≈ 'vs the past year's high').
    """
    peak = close.cummax() if window is None else close.rolling(window).max()
    return close / peak - 1.0
