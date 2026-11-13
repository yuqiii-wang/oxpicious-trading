"""study_kit.studies.macd — classic MACD (one calc, one file)."""
from __future__ import annotations

import pandas as pd

from study_kit.studies.ema import ema


def macd(
    close: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> pd.DataFrame:
    """The classic MACD (EMA(fast) − EMA(slow), its EMA(signal), the
    histogram) as a {macd, signal, hist} DataFrame — any span set via
    ``fast`` / ``slow`` / ``signal``.

    The CROSS EVENTS (the macd family's macd_cross_up/dn — the
    金叉/死叉 triggers with per-cell measured performance) are already
    MATERIALIZED in the analysis_signals tier: query
    analysis_signals.history_signals (the replayed trigger days) and
    analysis_signals.history_signal_performance (the dated forward-
    return audits) via study_kit.db.query — this function is the
    indicator-line math for custom spans, not the event history.
    """
    line = ema(close, fast) - ema(close, slow)
    sig = line.ewm(span=signal, adjust=False).mean()
    return pd.DataFrame({"macd": line, "signal": sig,
                         "hist": line - sig})
