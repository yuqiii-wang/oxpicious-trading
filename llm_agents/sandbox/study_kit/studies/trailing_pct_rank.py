"""study_kit.studies.trailing_pct_rank — own-history percentile (one file)."""
from __future__ import annotations

import pandas as pd


def trailing_pct_rank(s: pd.Series, w: int) -> pd.Series:
    """Each point's percentile rank (0..1) within its own trailing
    w-point window — the house anchoring convention: 'how unusual is
    today vs this security's own past year', never an absolute bar.
    For the 9 standard MA-spread gaps at window 252 the rank is already
    materialized — ``study_kit.db.fetch_spread_stats`` retrieves it.
    """
    return s.rolling(w).rank(pct=True)
