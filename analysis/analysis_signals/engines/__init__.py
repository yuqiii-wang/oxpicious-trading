"""Engine registry for analysis.analysis_signals (one family per
package under engines/signal_families/, dispatched through this
registry — never if/else'd over). The SignalEngine base (engines._base)
owns each family's complete lifecycle across its single-purpose mixin
layers (_snapshots / _store / _frame / _primitives).

TWO families: mov_rsi (the RSI percentile family) and boll_std (the
fixed-σ Bollinger band-breach family, 2026-10-07 — the excess σ-depth
over the 2 / 2.5 / 3σ bands replaces the percentile axis). The plain
gate at the robustness floors (engines/_frame.detect) is the whole
gate for every family.
"""
from __future__ import annotations

from analysis.analysis_signals.engines._base import RunStats, SignalEngine
from analysis.analysis_signals.engines.signal_families import (
    BollStdEngine,
    MovRsiEngine,
)

# signal_type → engine class (insertion order = pipeline order).
ENGINES: dict[str, type[SignalEngine]] = {
    MovRsiEngine.signal_type: MovRsiEngine,
    BollStdEngine.signal_type: BollStdEngine,
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
