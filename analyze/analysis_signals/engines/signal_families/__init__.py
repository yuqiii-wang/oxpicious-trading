"""Signal family engines (analyze.analysis_signals.engines.
signal_families) — one package per family (mov_rsi since the
2026-10-05 retirement of the mov_std / mov_pairs packages).

Each family subclasses engines._base SignalEngine and supplies its
three data phases (fetch_buckets / fetch_values / build); the
registry (engines/__init__) dispatches them — never if/else'd over.
"""
from analyze.analysis_signals.engines.signal_families.mov_rsi import (
    MovRsiEngine,
)

__all__ = [
    "MovRsiEngine",
]
