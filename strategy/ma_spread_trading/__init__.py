"""strategy.ma_spread_trading — MA-spread crossover backtest strategy.

Entry: MA5/MA60 golden cross confirmed by price>MA60, RSI not overbought,
and rising turnover. Exit: death cross, RSI overbought (take-profit), or
stop-loss below MA60. Fills same-day at a worst-case OHLC price (no
look-ahead).

The signal layer (data read + MA algo + consolidated b/s confidence)
lives in ``strategy._signal``; the execution layer (fills, slippage,
fees, portfolio accounting) lives in ``strategy._trading``. This package
wires them together and holds the trading params (see config.STRATEGY_PARAMS)
plus the CLI entry point.
"""
