"""study_kit.studies.roc — n-period rate of change (one calc, one file)."""
from __future__ import annotations

import pandas as pd


def roc(close: pd.Series, n: int = 20) -> pd.Series:
    """n-period rate of change, in percent (``n`` sets the anchor)."""
    return close.pct_change(n) * 100.0
