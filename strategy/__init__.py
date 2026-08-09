"""strategy — Trading strategy backtest packages.

Each sub-package implements one strategy, reading history-aware analysis
columns from the ``analysis`` schema and recording trade decisions into the
``strategy`` schema (strategy.strategy_seq + strategy.trade_decision).

Run a strategy via ``python -m strategy.<name>``.
"""
