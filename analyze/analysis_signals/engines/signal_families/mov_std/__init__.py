"""mov_std signal-family engine (analyze.analysis_signals.engines.
signal_families.mov_std) — re-exports the engine; the implementation
sits in _engine (identity + phases), _strategies (strategy rows) and
_history (history rows)."""
from analyze.analysis_signals.engines.signal_families.mov_std._engine import (
    MovStdEngine,
)

__all__ = ["MovStdEngine"]
