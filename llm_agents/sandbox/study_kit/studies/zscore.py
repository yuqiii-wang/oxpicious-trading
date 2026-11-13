"""study_kit.studies.zscore — trailing-window z-score (one calc, one file)."""
from __future__ import annotations

import pandas as pd


def zscore(s: pd.Series, w: int, ddof: int = 1) -> pd.Series:
    """Each point's z-score against its own trailing w-point window
    (``ddof`` picks sample (1) vs population (0) std). For the standard
    MA-spread gaps at window 252, the value is already materialized —
    ``study_kit.db.fetch_spread_stats`` retrieves it without compute.
    """
    mean = s.rolling(w).mean()
    std = s.rolling(w).std(ddof=ddof)
    return (s - mean) / std
