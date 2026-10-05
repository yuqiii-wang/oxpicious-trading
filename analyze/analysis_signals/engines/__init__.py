"""Engine registry for analyze.analysis_signals (one family per
package under engines/signal_families/, dispatched through this
registry — never if/else'd over). The SignalEngine base (engines._base)
owns each family's complete lifecycle across its single-purpose mixin
layers (_snapshots / _store / _frame / _primitives).

mov_rsi is the ONLY family since the 2026-10-05 retirement of the
mov_std / mov_pairs / mov_pairs_ema engines (their forecast families
went with them) and of the final SignalQuality gate
(engines/_quality.py) — the plain gate at the robustness floors
(engines/_frame.detect) is the whole gate now.
"""
from __future__ import annotations

from analyze.analysis_signals.engines._base import RunStats, SignalEngine
from analyze.analysis_signals.engines.signal_families import (
    MovRsiEngine,
)

# signal_type → engine class (insertion order = pipeline order).
ENGINES: dict[str, type[SignalEngine]] = {
    MovRsiEngine.signal_type: MovRsiEngine,
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
