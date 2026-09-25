"""mov_pairs signal-family configuration (shared by the two pair-cross
engines: mov_pairs / mov_pairs_ema — each covering BOTH its fast legs,
the ma5/ema6 indicator leg and the close-price leg).

Emission slice: BOTH cross sides on 60d-or-longer slow legs (the full
study grid), every regime split (regime_state is a signal_strategies PK
member — each split registers on its own gate pass). The cross-DOWN
(death cross, side bottom → buy) and the cross-UP (golden cross,
side top → sell) are the family's two complementary mean-reversion
events — every sign flip of the daily spread flags exactly once, one
row per cross (the 2026-09 widening from the bottom-only slice; the
forecast layer always computed both sides — top-side buckets pass the
sign-aligned gate at rates comparable to bottom's).

The pair-window grids stay single-sourced in analysis_forecasts' config
and are re-exported here as the family's own grids.
"""
from analyze.analysis_forecasts.config import (
    MOV_PAIRS_EMA_WINDOWS,
    MOV_PAIRS_WINDOWS,
)

# The emission slice: slow-leg windows >= PAIRS_SIGNAL_WINDOW_MIN,
# sides in PAIRS_SIGNAL_SIDES (top = cross up → sell, bottom =
# cross down → buy).
PAIRS_SIGNAL_WINDOW_MIN = 60
PAIRS_SIGNAL_SIDES = ("bottom", "top")

__all__ = [
    "PAIRS_SIGNAL_WINDOW_MIN",
    "PAIRS_SIGNAL_SIDES",
    "MOV_PAIRS_WINDOWS",
    "MOV_PAIRS_EMA_WINDOWS",
]
