"""Configuration for the MA-spread crossover backtest strategy.

Strategy summary
----------------
  Entry (BUY):  MA5/MA60 golden cross (ma5_vs_ma60: prev<=0 -> curr>0),
                confirmed by:
                  - price_vs_ma60 > 0            (price above MA60, uptrend)
                  - rsi_14days < entry_rsi_max   (not overbought, room to run)
                  - trading_amt_ma5_slope > 0    (turnover rising, liquidity)
  Exit  (SELL): after min_holding_period, on rising-edge of ANY of:
                  - MA5/MA60 death cross (ma5_vs_ma60: prev>=0 -> curr<0)
                  - rsi_14days > exit_rsi_max    (overbought take-profit)
                  - price_vs_ma60 < stop_loss    (stop-loss below MA60)

  Execution:    signal computed at CLOSE of date T (all inputs available at T);
                order FILLS at the OPEN of T+1 (next trading day). No look-ahead.
  Position:     unlimited BUYs accumulate to position (no capital cap). Each
                BUY/SELL decision carries a 0-100 confidence score (stored as
                trade_decision.qty):
                  BUY:  deploy (confidence/100) * buy_notional yuan → buy shares.
                        Position accumulates freely across multiple BUYs.
                  SELL: close (confidence/100) of CURRENT POSITION → sell shares.
                        (confidence = fraction of position to close, NOT fraction
                        of capital — fixes the asymmetry where SELL confidence
                        was measured against capital but BUY against the same.)
                        SELL is always capped at current position (no shorting).
                No fixed capital budget; total_buy_cost (sum of all BUY costs)
                is computed after the backtest and replaces the capital concept.
                Total Return = final_cash / total_buy_cost (% return on invested).
  Costs:        commission_rate on both sides; stamp_duty_rate on SELL only
                (A-share convention).

Strategy-specific config (this module):
  - STRATEGY_NAME, STRATEGY_PARAMS, signal column lists
Shared config (strategy._common.constants):
  - ALL_SEC_TYPES, DEFAULT_SEC_TYPE, DEFAULT_CODES, BATCH_SIZE,
    SEC_TYPE_BASIC_STATS_TABLE, DEFAULT_BUY_NOTIONAL
"""
from __future__ import annotations

# Re-export shared constants so callers can import everything from this module.
from strategy._common.constants import (  # noqa: F401
    ALL_SEC_TYPES,
    DEFAULT_SEC_TYPE,
    DEFAULT_CODES,
    BATCH_SIZE,
    SEC_TYPE_BASIC_STATS_TABLE,
    DEFAULT_BUY_NOTIONAL,
)

# ---------------------------------------------------------------------------
# Strategy identity
# ---------------------------------------------------------------------------
STRATEGY_NAME = "ma_spread_trading"

# ---------------------------------------------------------------------------
# Tunable strategy parameters (overridable via CLI in __main__)
# ---------------------------------------------------------------------------
STRATEGY_PARAMS = {
    # MA pair: the core signal is ma5_vs_ma60 (MA5 vs MA60 spread).
    "ma_short": 5,
    "ma_long": 60,

    # Entry confirmation thresholds.
    "entry_rsi_max": 70.0,        # don't BUY if RSI(14) >= this (overbought)
    "entry_price_vs_ma_long_min": 0.0,  # price must be above MA60

    # Exit thresholds (evaluated only after min_holding_period).
    "exit_rsi_max": 75.0,         # SELL if RSI(14) > this (take-profit)
    "stop_loss_vs_ma_long": -0.05,  # SELL if price_vs_ma60 < this (-5% below MA60)

    # One position per code (no averaging in).
    "max_open_positions_per_code": 1,

    # Holding rule.
    "min_holding_period": 7,      # trading days before a SELL is allowed

    # Transaction costs (A-share convention).
    "commission_rate": 0.0003,    # 0.03% broker commission, both sides
    "stamp_duty_rate": 0.001,     # 0.1% stamp duty, SELL side only

    # Buy notional (yuan). Each BUY deploys (confidence/100) * buy_notional.
    # No fixed capital budget — BUYs accumulate freely; total_buy_cost is
    # computed after the backtest. 100,000 yuan per trade at confidence=100.
    "buy_notional": DEFAULT_BUY_NOTIONAL,

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
# Split by source table so fetch.py can apply the correct alias prefix (d. vs r.).
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
# Combined tuple (kept for backwards compat / iteration over all signal cols).
SIGNAL_COLUMNS = DETAIL_SIGNAL_COLUMNS + RSI_SIGNAL_COLUMNS
