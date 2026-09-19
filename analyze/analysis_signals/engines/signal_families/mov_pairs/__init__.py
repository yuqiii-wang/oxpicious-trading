"""mov_pairs signal-family engines (analyze.analysis_signals.engines.
signal_families.mov_pairs) — re-exports the two pair-cross engines;
the implementation sits in _engine (identity + phases, shared by both
families), _strategies (strategy rows) and _history (history rows)."""
from analyze.analysis_signals.engines.signal_families.mov_pairs._engine import (
    MovPairsEmaEngine,
    MovPairsEngine,
)

__all__ = [
    "MovPairsEngine",
    "MovPairsEmaEngine",
]
