"""study_kit.studies.ma_alignment — 多头排列 MA-stack verdict (one file)."""
from __future__ import annotations

from typing import Tuple

import numpy as np
import pandas as pd

from study_kit.studies.sma import sma


def ma_alignment(
    close: pd.Series,
    windows: Tuple[int, ...] = (20, 60, 120),
) -> pd.Series:
    """Per-row alignment verdict over close + SMA(``windows``): strictly
    close > ma(1st) > ma(2nd) > … = 'bullish' (多头排列 — pullbacks into
    the stack are the buyable zones), the reverse = 'bearish'
    (rallies sold into), anything else = 'mixed'. Any window stack via
    ``windows`` (order matters — outermost leg first).
    """
    cols = {"close": close}
    for w in windows:
        cols[f"ma{w}"] = sma(close, w)
    frame = pd.DataFrame(cols)
    ordered = ["close", *(f"ma{w}" for w in windows)]
    bullish = frame[ordered[0]].notna()
    bearish = bullish.copy()
    for prev, nxt in zip(ordered, ordered[1:]):
        bullish &= frame[prev] > frame[nxt]
        bearish &= frame[prev] < frame[nxt]
    return pd.Series(
        np.select([bullish, bearish], ["bullish", "bearish"],
                  default="mixed"),
        index=close.index,
    )
