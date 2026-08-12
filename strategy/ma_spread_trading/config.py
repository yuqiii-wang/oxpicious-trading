"""Configuration for the MA-spread crossover backtest strategy.

Strategy identity + trading/execution parameters. The MA signal parameters
and analysis-column lists (what data to read) live in
``strategy._signal.config``; this module merges them with the trading
params into the single STRATEGY_PARAMS dict consumed by the runner.

  Entry (BUY):  MA5/MA{long} golden cross confirmed by price>MA{long},
                RSI not overbought, rising turnover.
  Exit  (SELL): rising-edge of death cross / RSI overbought / stop-loss.

  Signal layer:   strategy._signal (reads data per MA requirements, runs
                  the MA trading algo, consolidates to a singular
                  signal_confidence ∈ [-100, 100] → strategy._trading).
  Execution:      strategy._trading (worst-case OHLC fills, slippage,
                  fees, position / cash / realized-P&L, daily state).
  Position:       unlimited BUYs accumulate; SELL capped at current
                  position (no shorting). No fixed capital budget;
                  total_buy_cost (peak deployed) computed after backtest.
                  Total Return = final_cash / total_buy_cost.
"""
from __future__ import annotations

# Re-export shared constants so callers can import everything from this module.
from strategy._common.constants import (  # noqa: F401
    ALL_SEC_TYPES,
    DEFAULT_CODES,
    BATCH_SIZE,
    SEC_TYPE_BASIC_STATS_TABLE,
    DEFAULT_BUY_NOTIONAL,
)
# Signal params + column lists live in the signal layer now.
from strategy._signal.config import (  # noqa: F401
    SIGNAL_PARAMS,
    DETAIL_SIGNAL_COLUMNS,
    RSI_SIGNAL_COLUMNS,
    SIGNAL_COLUMNS,
)

# ---------------------------------------------------------------------------
# Strategy identity
# ---------------------------------------------------------------------------
STRATEGY_NAME = "ma_spread_trading"

# ---------------------------------------------------------------------------
# Trading / execution parameters (signal params come from _signal.config).
# ---------------------------------------------------------------------------
TRADING_PARAMS = {
    # One position per code (no averaging in).
    "max_open_positions_per_code": 1,

    # Holding rule.
    "min_holding_period": 7,      # trading days before a SELL is allowed

    # Buy notional (yuan). Each BUY deploys (confidence/100) * buy_notional.
    # No fixed capital budget — BUYs accumulate freely; total_buy_cost is
    # computed after the backtest. 100,000 yuan per trade at confidence=100.
    "buy_notional": DEFAULT_BUY_NOTIONAL,
}

# Merged params dict consumed by the runner + engine. Signal sub-params
# (ma_short/ma_long, entry/exit thresholds, confidence thresholds/weights)
# come from SIGNAL_PARAMS; trading sub-params from TRADING_PARAMS.
STRATEGY_PARAMS = {**SIGNAL_PARAMS, **TRADING_PARAMS}
