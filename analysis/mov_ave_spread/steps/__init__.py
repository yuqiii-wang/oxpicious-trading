"""Internal step pipelines for analysis.mov_ave_spread — one table one
module, one row per entry (each entry is a :class:`TableStep` subclass
whose ``execute`` carries the pipeline; the shared skeleton lives in
``_common.steps.TableStep``):

  detail                 — build_detail_frame (the parent detail-table
                           step's wide build; inserted via __main__)
  rsi                    — analysis.mov_ave_rsi (Wilder RSI)
  rsi_ranks              — analysis.mov_ave_rsi_ranks (per-code RSI
                           history ranks; wholesale rebuild hooked into
                           the RSI step's post-compute)
  trend_episode_days     — analysis.trend_episode_days (the trends
                           component's RSI tail-episode store; wholesale
                           rebuild hooked into the RSI step's post-write
                           — lives in analysis.trends, imported here
                           only for the run hook)
  holiday                — analysis.mov_ave_rsi_holiday
  ema                    — analysis.mov_ave_spreads_detail_ema
  ohlc                   — analysis.mov_ave_spreads_roof_and_floor_lines
  ohlc_gaps              — analysis.mov_ave_spreads_detail_ohlc_gaps
  roof_floor_estimate    — analysis.mov_ave_spreads_roof_floor_estimates
                           (quantitative level estimates per method; the
                           step PACKAGE lives one level up in
                           roof_floor_estimate/, re-exported here)
  roof_floor_rebound     — analysis.mov_ave_spreads_roof_floor_rebounds
                           (the rebound-audit: recorded extremes -> re-
                           bound freq/amplitude + retest hold/break;
                           wholesale rebuild — package one level up in
                           roof_floor_rebound/, re-exported here)
  trading_amt            — analysis.mov_ave_trading_amt
  trading_amt_ratios     — analysis.mov_ave_trading_amt_ratios
  price_vs_amt           — analysis.mov_ave_price_vs_amt (px_vol registry)
  px_vol                 — price_vs_amt's feature layer + source fetch
  momentum               — analysis.mov_ave_momentum (fixed 5/20d +
                           volatility-adaptive short/mid/long lookbacks
                           per (code, regime); wholesale rebuild)
  high_low_pct           — analysis.mov_ave_high_low_pct
  high_low_pct_streaks   — analysis.mov_ave_high_low_pct_streaks
  codes_summary          — analysis.mov_ave_spreads_codes refresh

Standard steps inherit the template ``execute`` (target-date detection /
force purge / incremental filter / chunked insert / identity upsert);
wholesale-rebuild steps (price_vs_amt, momentum, roof_floor_rebound,
high_low_pct, high_low_pct_streaks) override ``execute`` with their own
flows.
"""
from analysis.mov_ave_spread.steps.detail import build_detail_frame
from analysis.mov_ave_spread.steps.rsi import RsiStep, RSI_TABLE
from analysis.mov_ave_spread.steps.rsi_ranks import (
    RSI_RANKS_TABLE,
    rebuild_rsi_ranks,
)
from analysis.mov_ave_spread.steps.holiday import HolidayStep
from analysis.mov_ave_spread.steps.ema import EmaStep
from analysis.mov_ave_spread.steps.ohlc import (
    OhlcStep,
    build_ohlc_long_frame,
    compute_ohlc_columns,
    find_ohlc_repair_dates,
)
from analysis.mov_ave_spread.steps.ohlc_gaps import OhlcGapsStep
from analysis.mov_ave_spread.roof_floor_estimate import (
    RoofFloorEstimateStep,
)
from analysis.mov_ave_spread.roof_floor_rebound import (
    RoofFloorReboundStep,
)
from analysis.mov_ave_spread.steps.trading_amt import TradingAmtStep
from analysis.mov_ave_spread.steps.trading_amt_ratios import (
    TradingAmtRatiosStep,
)
from analysis.mov_ave_spread.steps.price_vs_amt import PriceVsAmtStep
from analysis.mov_ave_spread.steps.momentum import (
    MOMENTUM_TABLE,
    MomentumStep,
)
from analysis.mov_ave_spread.steps.high_low_pct import (
    HighLowPctStep,
    find_missing_high_low_pct_pairs,
)
from analysis.mov_ave_spread.steps.high_low_pct_streaks import (
    HighLowPctStreaksStep,
)
from analysis.mov_ave_spread.steps.codes_summary import refresh_codes_summary

__all__ = [
    "build_detail_frame",
    "RsiStep", "RSI_TABLE",
    "RSI_RANKS_TABLE", "rebuild_rsi_ranks",
    "HolidayStep",
    "EmaStep",
    "OhlcStep", "find_ohlc_repair_dates", "compute_ohlc_columns",
    "build_ohlc_long_frame",
    "OhlcGapsStep",
    "RoofFloorEstimateStep",
    "RoofFloorReboundStep",
    "TradingAmtStep",
    "TradingAmtRatiosStep",
    "PriceVsAmtStep",
    "MOMENTUM_TABLE", "MomentumStep",
    "HighLowPctStep", "find_missing_high_low_pct_pairs",
    "HighLowPctStreaksStep",
    "refresh_codes_summary",
]
