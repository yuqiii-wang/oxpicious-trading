"""Engine registry for analyze.analysis_signals (one family per
package under engines/signal_families/, dispatched through this
registry — never if/else'd over). The SignalEngine base (engines._base)
owns each family's complete lifecycle across its single-purpose mixin
layers (_months / _store / _frame / _primitives)."""
from __future__ import annotations

from analyze.analysis_signals.engines._base import RunStats, SignalEngine
from analyze.analysis_signals.engines.signal_families import (
    MovPairsEmaEngine,
    MovPairsEngine,
    MovRsiEngine,
    MovStdEngine,
)

# signal_type → engine class (insertion order = pipeline order).
ENGINES: dict[str, type[SignalEngine]] = {
    MovRsiEngine.signal_type: MovRsiEngine,
    MovStdEngine.signal_type: MovStdEngine,
    MovPairsEngine.signal_type: MovPairsEngine,
    MovPairsEmaEngine.signal_type: MovPairsEmaEngine,
}


def engines_for(metrics: frozenset[str] | None) -> list[SignalEngine]:
    """The engines for the selected --metrics stage keys (None = all),
    in pipeline order."""
    return [
        engine()
        for engine in ENGINES.values()
        if metrics is None or engine.stage_key in metrics
    ]


__all__ = ["ENGINES", "engines_for", "SignalEngine", "RunStats"]
