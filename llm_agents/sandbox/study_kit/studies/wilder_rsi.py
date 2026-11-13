"""study_kit.studies.wilder_rsi — Wilder RSI (one calc, one file)."""
from __future__ import annotations

import pandas as pd


def wilder_rsi(close: pd.Series, w: int = 14) -> pd.Series:
    """Wilder RSI over window ``w`` (the repo's RSI convention: Wilder
    smoothing, alpha = 1/w). Zones: >70 overbought, <30 oversold —
    read as exhaustion/capitulation context, not bare signals."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / w, adjust=False,
                        min_periods=w).mean()
    avg_loss = loss.ewm(alpha=1.0 / w, adjust=False,
                        min_periods=w).mean()
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)
