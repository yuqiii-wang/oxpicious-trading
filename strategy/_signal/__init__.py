"""Signal layer: data reading + MA trading algorithm + consolidated b/s
confidence value.

This package is the signal-generation peer of ``strategy._trading`` (the
execution layer). Pipeline:

  1. :func:`fetch_signal_data` — read data per MA-trading requirements.
     The column lists in :mod:`_signal.config` declare WHAT data to read
     (MA gaps, slopes, σ, turnover-MAs, RSI); this step materializes them
     into a per-(code, date) DataFrame.
  2. :func:`apply_signals` — run the MA trading algorithm (MA5/MA{long}
     cross detection, entry/exit rules, confidence scoring) and CONSOLIDATE
     the result into a singular signed ``signal_confidence`` ∈ [-100, 100]:
       > 0 → BUY, value = buy confidence
       < 0 → SELL (rising-edge filtered), value = -sell confidence
       = 0 → no signal
  3. :func:`build_signal_reason` — human-readable reason text (MA-specific).

The ``strategy._trading`` engine consumes ONLY ``signal_confidence`` (+ the
auxiliary ``signal_value`` magnitude), so the execution layer stays
signal-agnostic: a different signal layer could feed the same engine by
producing a ``signal_confidence`` column.
"""
from __future__ import annotations

from strategy._signal.config import (  # noqa: F401
    SIGNAL_PARAMS,
    DETAIL_SIGNAL_COLUMNS,
    RSI_SIGNAL_COLUMNS,
    SIGNAL_COLUMNS,
)
from strategy._signal.fetch import fetch_signal_data  # noqa: F401
from strategy._signal.algo import (  # noqa: F401
    apply_signals,
    consolidate_signal,
)
from strategy._signal.reason import build_signal_reason  # noqa: F401

__all__ = [
    "SIGNAL_PARAMS",
    "DETAIL_SIGNAL_COLUMNS",
    "RSI_SIGNAL_COLUMNS",
    "SIGNAL_COLUMNS",
    "fetch_signal_data",
    "apply_signals",
    "consolidate_signal",
    "build_signal_reason",
]
