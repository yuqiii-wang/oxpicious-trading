"""Base signal tuning — inherited by ALL sub-algos via AlgoBase.run_backtest.

Every algo produces a signed ``signal_confidence`` ∈ [-100, 100] (BUY > 0,
SELL < 0). Signals with magnitude below ``SIGNAL_CONFIDENCE_THRESHOLD`` are
execution noise — a sub-threshold confidence rounds to a near-zero qty
(BUY qty = confidence, so conf=3 → qty=3 ≈ 3% of a 100K notional = ~3K yuan)
that produces dust trades and meaningless P&L. This module zeroes them out
BEFORE the engine runs, so:

  - BUYs with 0 < conf < THRESHOLD → ignored (no trade)
  - SELLs with -THRESHOLD < conf < 0 → ignored (no trade)
  - |conf| ≥ THRESHOLD → unchanged

Both ``signal_confidence`` and the auxiliary ``signal_value`` are zeroed
together so the stored ``signal_value`` never reflects a signal that was
ignored by the tuning.

This is applied in three places (all inherited automatically — no per-algo
change required):

  1. ``AlgoBase.run_backtest``            — single-algo (binary) path
  2. ``AlgoSignalCollector.run_backtest``  — mixed-mode (blended) path
  3. ``fault_tolerance.run_ft_stress``     — re-run on stressed OHLC, so
     FT magnitudes correctly reflect "trade removed under stress" when the
     stressed conf drops below the threshold.
"""
from __future__ import annotations

import pandas as pd

# Minimum |signal_confidence| required to fire a trade. Signals below this
# magnitude are treated as no-signal (zeroed) before the engine runs.
SIGNAL_CONFIDENCE_THRESHOLD: float = 5.0


def tune_signals(df: pd.DataFrame, threshold: float = SIGNAL_CONFIDENCE_THRESHOLD) -> pd.DataFrame:
    """Zero out sub-threshold ``signal_confidence`` (and ``signal_value``).

    Operates IN-PLACE on ``df`` (and returns it for chaining). Rows where
    ``|signal_confidence| < threshold`` get both ``signal_confidence`` and
    ``signal_value`` set to 0.0 — the engine treats 0 as "no signal" and
    skips the bar. Rows with NaN/missing signal_confidence are untouched
    (the engine already skips them).

    Safe to call on an empty df or one without the columns (no-op).
    """
    if df.empty or "signal_confidence" not in df.columns:
        return df

    sig = df["signal_confidence"]
    # mask = real signal with magnitude below threshold (exclude NaN).
    mask = sig.notna() & (sig.abs() < float(threshold))
    if mask.any():
        df.loc[mask, "signal_confidence"] = 0.0
        if "signal_value" in df.columns:
            df.loc[mask, "signal_value"] = 0.0
    return df


__all__ = ["SIGNAL_CONFIDENCE_THRESHOLD", "tune_signals"]
