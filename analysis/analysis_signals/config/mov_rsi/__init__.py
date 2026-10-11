"""mov_rsi signal-family configuration.

Emission slice: the top/bottom-{1,2,3}% RSI percentile buckets, both
sides, every regime split (regime_state is a signal_thresholds PK
member — each split registers on its own gate pass). The 2026-09
quality-v2 calibration widened the slice from 1% only: the 2%/3% tails
carry ≈ 0.9–1.2% blended edge at 6–9 occurrences — the sample the
robustness floors demand (docs/signal_quality_v2_proposal.md).

The RSI window grid is single-sourced in mov_ave_spread's rsi step
(the owner of analysis.mov_ave_rsi) and re-exported here as the
family's own grid, beside the step's episode re-entry exit K — BOTH
signal tiers' run boundary (the threshold tier's streak-merge
debounces with it, so its occurrence count is the same episode count
the curve tier's mu is measured over; 2026-10-07 tier alignment).
"""
from analysis.mov_ave_spread.steps.rsi import RSI_WINDOWS
from analysis.mov_ave_spread.steps.rsi_typical import RSI_REENTRY_EXIT_DAYS

# The emission slice: the emitted RSI percentile widths (percent).
RSI_PCTS_EMIT = (1, 2, 3)

# Bucket sides: top = overbought (highest-pct% RSI days) → sell,
#               bottom = oversold (lowest-pct% RSI days) → buy.
RSI_SIDES = ("top", "bottom")

__all__ = [
    "RSI_PCTS_EMIT", "RSI_WINDOWS", "RSI_SIDES", "RSI_REENTRY_EXIT_DAYS",
]
