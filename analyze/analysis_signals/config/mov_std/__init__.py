"""mov_std signal-family configuration.

Emission slice: the Bollinger-breach buckets on 20d-or-longer MA/σ
windows at 2.0σ-or-tighter bands, both sides (upper/lower), every regime
split (regime_state is a signal_strategies PK member — each split
registers on its own gate pass).

The MA-window / σ-multiple grids stay single-sourced in
analysis_forecasts' config and are re-exported here as the family's
own grids.
"""
from analyze.analysis_forecasts.config import MA_WINDOWS, STD_MULTIPLES

# The emission slice: MA/σ windows >= STD_MA_WINDOW_MIN at σ
# multiples >= STD_K_MIN. The 5d window stays excluded (MA_WINDOWS =
# (5, 20, 60) — 20d is the classic Bollinger scale, 60d the slower
# regime band).
STD_MA_WINDOW_MIN = 20
STD_K_MIN = 2.0

__all__ = ["STD_MA_WINDOW_MIN", "STD_K_MIN", "MA_WINDOWS", "STD_MULTIPLES"]
