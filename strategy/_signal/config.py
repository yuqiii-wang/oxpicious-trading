"""Signal-layer configuration for the MA-spread strategy.

Owns the MA trading signal parameters + the analysis-column lists that
declare WHAT DATA the signal layer needs to read (the "check what data to
read by MA trading requirements" step). Trading/execution parameters
(min_holding_period, buy_notional, ...) live in the strategy package
(ma_spread_trading.config); this module holds only the signal layer.

Signal summary
--------------
  Entry (BUY):  MA5/MA{long} golden cross confirmed by price>MA{long},
                RSI not overbought, and rising turnover.
  Exit  (SELL): rising-edge of ANY of: death cross, RSI overbought,
                stop-loss below MA{long}.

  Consolidated output (produced by _signal.algo):
      signal_confidence ∈ [-100, 100]
        > 0  → BUY signal,  value = buy confidence
        < 0  → SELL signal, value = -sell confidence (rising-edge filtered)
        = 0  → no signal
  The strategy._trading engine reads ONLY signal_confidence (+ the
  auxiliary signal_value magnitude), keeping the execution layer
  signal-agnostic.
"""
from __future__ import annotations

# Re-exported so _signal.fetch can resolve the per-sec_type basic_stats
# table (source of OHLC fill prices) from one place.
from strategy._common.constants import (  # noqa: F401
    SEC_TYPE_BASIC_STATS_TABLE,
)

# ---------------------------------------------------------------------------
# MA trading signal parameters
# ---------------------------------------------------------------------------
# MA pair: the core signal is ma5_vs_ma60 (MA5 vs MA60 spread).
SIGNAL_PARAMS = {
    "ma_short": 5,
    "ma_long": 60,

    # Entry confirmation thresholds.
    "entry_rsi_max": 70.0,              # don't BUY if RSI(14) >= this
    "entry_price_vs_ma_long_min": 0.0,  # price must be above MA{long}

    # Exit thresholds (evaluated only after min_holding_period).
    "exit_rsi_max": 75.0,               # SELL if RSI(14) > this (take-profit)
    "stop_loss_vs_ma_long": -0.05,      # SELL if price_vs_ma60 < this (-5%)

    # Confidence scoring (0-100; stored as trade_decision.qty).
    #   confidence_thresholds: divisors used to normalize each sub-score to
    #                          [0, 1]. A sub-score equals 1.0 when its raw
    #                          value reaches the threshold.
    #   confidence_weights: blend weights per sub-score. BUY uses a fixed
    #                       blend (all four conditions must fire); SELL uses
    #                       a confluence blend (only fired triggers
    #                       contribute, re-normalized by fired-weight sum).
    "confidence_thresholds": {
        # BUY sub-scores
        "ma_spread":           0.05,   # |ma5_vs_ma60| = 5% → full strength
        "price_above_ma_long": 0.10,   # price_vs_ma60 = 10% above → full
        "rsi_room":            30.0,   # 30 RSI points of room → full
        "turnover_slope":      0.10,   # trading_amt_ma5_slope = 0.10 → full
        # SELL sub-scores
        "rsi_excess":          10.0,   # 10 RSI points over exit_rsi_max → full
        "stop_loss_depth":     0.05,   # 5% below stop_loss_vs_ma_long → full
    },
    "confidence_weights": {
        "buy": {
            "ma_spread":     0.35,   # cross strength is the dominant signal
            "price_above":   0.25,   # trend confirmation
            "rsi_room":      0.25,   # momentum headroom
            "turnover":      0.15,   # liquidity buildup (weakest weight)
        },
        "sell": {
            "death_cross":   0.40,   # primary exit signal
            "rsi_excess":    0.30,   # take-profit strength
            "stop_loss":     0.30,   # stop-loss depth
        },
    },
}

# ---------------------------------------------------------------------------
# Analysis columns fetched for signals (history-aware subset).
# Split by source table so fetch.py can apply the correct alias prefix
# (d. for mov_ave_spreads_detail vs r. for mov_ave_rsi).
# These column lists ARE the "what data to read" declaration: they encode
# the MA trading requirements (MA gaps, slopes, σ, turnover-MAs, RSI).
# ---------------------------------------------------------------------------
DETAIL_SIGNAL_COLUMNS = (
    "price_vs_ma5", "price_vs_ma20", "price_vs_ma60", "price_vs_ma120",
    "price_vs_ma255",
    "ma5_vs_ma20", "ma5_vs_ma60", "ma5_vs_ma120", "ma5_vs_ma255",
    "ma5_slope", "ma60_slope",
    "std_20days", "std_60days",
    "trading_amt_ma5", "trading_amt_ma60",
    "trading_amt_ma5_slope", "trading_amt_ma60_slope",
    "trading_amt_market_share_vs_ma20",
)
RSI_SIGNAL_COLUMNS = (
    "rsi_6days", "rsi_10days", "rsi_14days", "rsi_20days",
    "gap_2days", "gap_3days",
    "date_of_last_extreme", "days_since_last_extreme", "gap_since_last_extreme",
)
# Combined tuple (kept for iteration over all signal cols).
SIGNAL_COLUMNS = DETAIL_SIGNAL_COLUMNS + RSI_SIGNAL_COLUMNS
