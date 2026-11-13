"""study_kit.studies.spread_report — the MA-spread regime read (compute
lane). For the STANDARD windows + trailing 252 the same numbers are
MATERIALIZED on analysis.mov_ave_spreads_detail ⋈
analysis.mov_ave_spreads_detail_trailing — retrieve them with
``study_kit.db.fetch_spread_stats`` and reach for this function only
for custom windows / thresholds.
"""
from __future__ import annotations

from typing import Tuple

import numpy as np
import pandas as pd

from study_kit.studies.sma import sma
from study_kit.studies.trailing_pct_rank import trailing_pct_rank
from study_kit.studies.zscore import zscore


def spread_report(
    close: pd.Series,
    code: str = "",
    windows: Tuple[int, ...] = (20, 60, 120, 252),
    trailing: int = 252,
    high_q: float = 0.90,
    low_q: float = 0.10,
) -> pd.DataFrame:
    """The MA-spread regime read, one row per window — the standard
    opening move of an MA study (value + own-history percentile + z +
    verdict per price−MA spread).

    verdict: 'stretched_high' / 'stretched_low' when the spread sits in
    the trailing window's top/bottom quantile band (``high_q`` /
    ``low_q`` — extension: premium invites profit-taking, deep discount
    is the oversold stretch snapbacks come from), 'neutral' otherwise —
    the SAME snake_case vocabulary the materialized verdict columns
    decode to. Rows need the window AND the trailing year realized — a
    shorter history returns fewer rows.
    """
    rows = []
    for w in windows:
        spread = close / sma(close, w) - 1.0
        rank = trailing_pct_rank(spread, trailing)
        z = zscore(spread, trailing)
        verdict = pd.Series(
            np.select(
                [rank >= high_q, rank <= low_q],
                ["stretched_high", "stretched_low"],
                default="neutral",
            ),
            index=close.index,
        )
        last = spread.last_valid_index()
        rows.append({
            "code": code,
            "window": w,
            "spread_pct": None if last is None else round(
                float(spread.loc[last]) * 100.0, 3),
            "pct_rank": None if last is None else round(
                float(rank.loc[last]), 3),
            "zscore": None if last is None else round(
                float(z.loc[last]), 2),
            "verdict": None if last is None else verdict.loc[last],
        })
    return pd.DataFrame(rows).dropna(subset=["verdict"]).set_index("window")
