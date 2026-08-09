"""strategy.ma_spread_trading — MA-spread crossover backtest strategy.

Entry: MA5/MA60 golden cross confirmed by price>MA60, RSI not overbought,
and rising turnover. Exit: death cross, RSI overbought (take-profit), or
stop-loss below MA60. Fills at next bar's open (no look-ahead).

See config.STRATEGY_PARAMS for the tunable parameters and
config.HISTORY_AWARENESS for which analysis columns are used vs excluded.
"""
