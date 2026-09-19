"""mov_rsi signal-family engine (analyze.analysis_signals.engines.
signal_families.mov_rsi) — re-exports the engine; the implementation
sits in _engine (identity + phases), _strategies (strategy rows) and
_history (history rows)."""
from analyze.analysis_signals.engines.signal_families.mov_rsi._engine import (
    MovRsiEngine,
)

__all__ = ["MovRsiEngine"]
