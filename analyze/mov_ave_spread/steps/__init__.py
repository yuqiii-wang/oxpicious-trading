"""Internal step pipelines for analyze.mov_ave_spread — one table one
module, one row per entry:

  detail                 — build_detail_frame (the parent detail-table
                           step's wide build; inserted via __main__)
  rsi                    — analysis.mov_ave_rsi (Wilder RSI)
  rsi_ranks              — analysis.mov_ave_rsi_ranks (per-code RSI
                           history ranks; wholesale rebuild hooked into
                           the RSI step's post-compute)
  holiday                — analysis.mov_ave_rsi_holiday
  ema                    — analysis.mov_ave_spreads_detail_ema
  ohlc                   — analysis.mov_ave_spreads_roof_and_floor_lines
  roof_floor_bands       — analysis.mov_ave_spreads_roof_and_floor_bands
  ohlc_gaps              — analysis.mov_ave_spreads_detail_ohlc_gaps
  trading_amt            — analysis.mov_ave_trading_amt
  trading_amt_ratios     — analysis.mov_ave_trading_amt_ratios
  price_vs_amt           — analysis.mov_ave_price_vs_amt (px_vol registry)
  px_vol                 — price_vs_amt's feature layer + source fetch
  high_low_pct           — analysis.mov_ave_high_low_pct
  high_low_pct_streaks   — analysis.mov_ave_high_low_pct_streaks
  codes_summary          — analysis.mov_ave_spreads_codes refresh

Standard steps delegate the pipeline skeleton (target-date detection /
force purge / incremental filter / chunked insert / identity upsert) to
``_common.steps.run_table_step``; wholesale-rebuild steps (price_vs_amt,
high_low_pct, high_low_pct_streaks) keep their own run_* flows.
"""
from analyze.mov_ave_spread.steps.detail import build_detail_frame
from analyze.mov_ave_spread.steps.rsi import run_rsi, RSI_TABLE
from analyze.mov_ave_spread.steps.rsi_ranks import (
    RSI_RANKS_TABLE,
    rebuild_rsi_ranks,
)
from analyze.mov_ave_spread.steps.holiday import run_holiday
from analyze.mov_ave_spread.steps.ema import run_ema
from analyze.mov_ave_spread.steps.ohlc import (
    build_ohlc_long_frame,
    compute_ohlc_columns,
    find_ohlc_repair_dates,
    run_ohlc,
)
from analyze.mov_ave_spread.steps.roof_floor_bands import (
    build_bands_long_frame,
    compute_bands_columns,
    run_roof_floor_bands,
)
from analyze.mov_ave_spread.steps.ohlc_gaps import run_ohlc_gaps
from analyze.mov_ave_spread.steps.trading_amt import run_trading_amt
from analyze.mov_ave_spread.steps.trading_amt_ratios import (
    run_trading_amt_ratios,
)
from analyze.mov_ave_spread.steps.price_vs_amt import run_price_vs_amt
from analyze.mov_ave_spread.steps.high_low_pct import (
    find_missing_high_low_pct_pairs,
    run_high_low_pct,
)
from analyze.mov_ave_spread.steps.high_low_pct_streaks import (
    run_high_low_pct_streaks,
)
from analyze.mov_ave_spread.steps.codes_summary import refresh_codes_summary

__all__ = [
    "build_detail_frame",
    "run_rsi", "RSI_TABLE",
    "RSI_RANKS_TABLE", "rebuild_rsi_ranks",
    "run_holiday",
    "run_ema",
    "run_ohlc", "find_ohlc_repair_dates", "compute_ohlc_columns",
    "build_ohlc_long_frame",
    "run_roof_floor_bands", "compute_bands_columns",
    "build_bands_long_frame",
    "run_ohlc_gaps",
    "run_trading_amt",
    "run_trading_amt_ratios",
    "run_price_vs_amt",
    "run_high_low_pct", "find_missing_high_low_pct_pairs",
    "run_high_low_pct_streaks",
    "refresh_codes_summary",
]
