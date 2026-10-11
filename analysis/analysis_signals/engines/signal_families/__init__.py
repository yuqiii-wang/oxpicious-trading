"""Signal family engines (analysis.analysis_signals.engines.
signal_families) — one package per family: mov_rsi (the RSI percentile
family) and boll_std (the fixed-σ Bollinger band-breach family — the
excess σ-depth over the bands replaces the percentile axis).

Each family subclasses engines._base SignalEngine and supplies its
three data phases (fetch_buckets / fetch_values / build); the
registry (engines/__init__) dispatches them — never if/else'd over.
"""
from analysis.analysis_signals.engines.signal_families.mov_rsi import (
    MovRsiEngine,
)
from analysis.analysis_signals.engines.signal_families.boll_std import (
    BollStdEngine,
)

__all__ = [
    "MovRsiEngine",
    "BollStdEngine",
]
