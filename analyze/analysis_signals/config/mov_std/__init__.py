"""mov_std signal-family configuration.

Emission slice: the Bollinger-breach buckets on 60d-or-longer MA/σ
windows at 2.0σ-or-tighter bands, both sides (upper/lower), every regime
split (regime_state is a signal_strategies PK member — each split
registers on its own gate pass).

The 20d window was REMOVED from the signals tier 2026-09-30 (the OOS
study temp_scripts/study_std20_oos.py: its upper/sell side realized
sign-flipped vs the claimed dir_ave — std20_2.5std sell claimed +271bp,
realized -246bp hit 44% — and its OOS events vanish for whole years,
std60 positive every year since 2022 with 3-5x the sample). The 20d
buckets STAY in analysis_forecasts — only the strategy emission slice
tightened to the slow regime band.

The MA-window / σ-multiple grids stay single-sourced in
analysis_forecasts' config and are re-exported here as the family's
own grids.
"""
from analyze.analysis_forecasts.config import MA_WINDOWS, STD_MULTIPLES

# The emission slice: MA/σ windows >= STD_MA_WINDOW_MIN at σ
# multiples >= STD_K_MIN. The 5d and 20d windows stay excluded
# (MA_WINDOWS = (5, 20, 60) — 20d is the classic Bollinger scale but
# failed the 2026-09-30 OOS study; 60d is the slower regime band).
STD_MA_WINDOW_MIN = 60
STD_K_MIN = 2.0

# The claim profile: every emitted window (>= STD_MA_WINDOW_MIN) is a
# SLOW band-swing claim — the SignalQuality gate holds all mov_std
# buckets to the 20d forward-mean alignment too
# (docs/signal_quality_v2_proposal.md).
SLOW_CLAIM = ("ma_window", STD_MA_WINDOW_MIN)

__all__ = [
    "STD_MA_WINDOW_MIN", "STD_K_MIN", "SLOW_CLAIM",
    "MA_WINDOWS", "STD_MULTIPLES",
]
