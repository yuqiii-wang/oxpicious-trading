"""mov_std signal-family configuration.

Emission slice: the Bollinger-breach buckets on 60d-or-longer MA/σ
windows at 2.0σ-or-tighter bands, both sides (upper/lower), BOTH hype
splits (is_market_hyped is a signal_strategies PK member — each split
registers on its own gate pass).

The MA-window / σ-multiple grids stay single-sourced in
analysis_forecasts' config and are re-exported here as the family's
own grids.
"""
from analyze.analysis_forecasts.config import MA_WINDOWS, STD_MULTIPLES

# The emission slice: MA/σ windows >= STD_MA_WINDOW_MIN at σ
# multiples >= STD_K_MIN.
STD_MA_WINDOW_MIN = 60
STD_K_MIN = 2.0

__all__ = ["STD_MA_WINDOW_MIN", "STD_K_MIN", "MA_WINDOWS", "STD_MULTIPLES"]
