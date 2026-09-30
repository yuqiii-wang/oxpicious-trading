"""mov_rsi signal-family configuration.

Emission slice: the top/bottom-{1,2,3}% RSI percentile buckets, both
sides, every regime split (regime_state is a signal_strategies PK
member — each split registers on its own gate pass). The 2026-09
quality-v2 calibration widened the slice from 1% only: the 2%/3% tails
carry ≈ 0.9–1.2% blended edge at 6–9 occurrences — the sample the
robustness floors demand
(docs/signal_quality_v2_proposal.md).

The RSI window grid stays single-sourced in analysis_forecasts' config
and is re-exported here as the family's own grid.
"""
from analyze.analysis_forecasts.config import RSI_WINDOWS

# The emission slice: the emitted RSI percentile widths (percent).
RSI_PCTS_EMIT = (1, 2, 3)

# The claim profile: rows whose window reaches SLOW_CLAIM_MIN carry a
# slow claim — their required forward-mean alignment includes the 20d
# leg (the SignalQuality gate's `slow` split). Fast windows (3/6/10)
# claim only the 5d reversal.
SLOW_CLAIM = ("rsi_window", 20)

__all__ = ["RSI_PCTS_EMIT", "RSI_WINDOWS", "SLOW_CLAIM"]
