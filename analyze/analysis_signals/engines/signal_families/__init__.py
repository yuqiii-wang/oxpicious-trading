"""Signal family engines (analyze.analysis_signals.engines.
signal_families) — one package per family (mov_rsi, mov_std,
mov_pairs, ...).

Each family subclasses engines._base SignalEngine and supplies its
three data phases (fetch_buckets / fetch_values / build); the
registry (engines/__init__) dispatches them — never if/else'd over.
The mov_pairs package exports the TWO pair-cross engines (mov_pairs /
mov_pairs_ema) over one shared lifecycle — each covering BOTH its
fast legs (the ma5/ema6 indicator leg and the close-price leg).
"""
from analyze.analysis_signals.engines.signal_families.mov_pairs import (
    MovPairsEmaEngine,
    MovPairsEngine,
)
from analyze.analysis_signals.engines.signal_families.mov_rsi import (
    MovRsiEngine,
)
from analyze.analysis_signals.engines.signal_families.mov_std import (
    MovStdEngine,
)

__all__ = [
    "MovRsiEngine",
    "MovStdEngine",
    "MovPairsEngine",
    "MovPairsEmaEngine",
]
