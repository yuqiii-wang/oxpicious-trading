"""mov_pairs signal-family configuration (shared by the two pair-cross
engines: mov_pairs / mov_pairs_ema — each covering BOTH its fast legs,
the ma5/ema6 indicator leg and the close-price leg).

Emission slice: the CROSS-DOWN (death cross, side bottom → buy) buckets
on 120d-or-longer slow legs, BOTH hype splits (is_market_hyped is a
signal_strategies PK member — each split registers on its own gate
pass). The 60d cross is excluded from the emission (the per-metric
slice: too noisy at signal granularity — price crossing a 60d leg
flips too often to be a regime cross), and the cross-UP (top) side is
not emitted (the families' directional claim is the death-cross
reversal; widening is a config change).

The pair-window grids stay single-sourced in analysis_forecasts' config
and are re-exported here as the family's own grids.
"""
from analyze.analysis_forecasts.config import (
    MOV_PAIRS_EMA_WINDOWS,
    MOV_PAIRS_WINDOWS,
)

# The emission slice: slow-leg windows >= PAIRS_SIGNAL_WINDOW_MIN,
# sides in PAIRS_SIGNAL_SIDES.
PAIRS_SIGNAL_WINDOW_MIN = 120
PAIRS_SIGNAL_SIDES = ("bottom",)

__all__ = [
    "PAIRS_SIGNAL_WINDOW_MIN",
    "PAIRS_SIGNAL_SIDES",
    "MOV_PAIRS_WINDOWS",
    "MOV_PAIRS_EMA_WINDOWS",
]
