"""mov_rsi signal-family configuration.

Emission slice: the top/bottom-1% RSI percentile buckets, both sides,
BOTH hype splits (is_market_hyped is a signal_strategies PK member —
each split registers on its own gate pass).

The RSI window grid stays single-sourced in analysis_forecasts' config
and is re-exported here as the family's own grid.
"""
from analyze.analysis_forecasts.config import RSI_WINDOWS

# The emission slice: the top/bottom-1% RSI percentile buckets.
RSI_PCT = 1

__all__ = ["RSI_PCT", "RSI_WINDOWS"]
