"""The consolidated TRENDS + EPISODES component
(analysis.trends — an independent package: every trend/episode concept
in the repo imports from HERE, never the other way round).

Owns three concerns, previously scattered across
analysis.analysis_signals (trends.py, engines/evaluation.py's
EpisodeDetector, config's episode constants) and
analysis.mov_ave_spread.steps.rsi_typical (the retired is_typical
write-back onto analysis.mov_ave_rsi):

  episodes.py      the shared episode ANATOMY — the closed-window run
                   segmentation (first-touch passes / re-entry passes /
                   episode size) + EpisodeDetector (the breach-run
                   FIRST-day machinery with the K-absorb, optionally
                   trend-aware) + the anatomy constants
                   (TYPICAL_ISOLATED_DAYS / REENTRY_EXIT_DAYS /
                   RSI_TYPICAL_BAND).
  trend_state.py   the family-agnostic per-(code, date) price TREND
                   classification (top / bottom the MA255-gap extremes,
                   rise / fall the RSI re-entry episodes, unknown the
                   unclaimed bucket — attach_trend) + its parameters.
  rsi_episode_days.py  the RSI reading of the episode anatomy as its
                   own store: analysis.trend_episode_days, one row per
                   (sec_type, code, date) with >= 1 in-band window,
                   keyed by the table's own PK — REPLACES the
                   is_typical_{W}days / typical_ep_size_{W}days columns
                   that used to ride analysis.mov_ave_rsi (removed).

DEPENDENCY RULE (the reason this package exists): ``analysis.*`` must
never import from ``analysis.analysis_signals`` — the signals tier is a
CONSUMER of the trend/episode primitives, not their owner. The
mov_ave_spread RSI step and the signals engines both import from here.

SQL: database/sql/analysis/trends/ (01_trend_days.sql — the price-trend
store moved out of the analysis_signals schema; 02_trend_episode_days.
sql — the RSI tail-episode store).
"""
from analysis.trends.episodes import (
    EpisodeDetector,
    REENTRY_EXIT_DAYS,
    RSI_TYPICAL_BAND,
    TYPICAL_ISOLATED_DAYS,
    rsi_tail_columns,
)
from analysis.trends.trend_state import (
    TREND_BOTTOM_Q,
    TREND_GAP_WINDOW,
    TREND_RSI_WINDOW,
    TREND_RSI_ZONE_SPREAD,
    TREND_TOP_Q,
    attach_trend,
)

__all__ = [
    # episodes
    "EpisodeDetector",
    "REENTRY_EXIT_DAYS",
    "RSI_TYPICAL_BAND",
    "TYPICAL_ISOLATED_DAYS",
    "rsi_tail_columns",
    # trend state
    "TREND_BOTTOM_Q",
    "TREND_GAP_WINDOW",
    "TREND_RSI_WINDOW",
    "TREND_RSI_ZONE_SPREAD",
    "TREND_TOP_Q",
    "attach_trend",
]
