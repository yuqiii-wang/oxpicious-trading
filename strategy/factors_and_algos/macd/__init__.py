"""strategy.factors_and_algos.macd — MACD (12/26/9) crossover mean-reversion.

A pluggable signal algo implementing :class:`strategy.factors_and_algos._algo.AlgoBase`.
Produces the two columns the execution engine in ``strategy._trading``
consumes (``signal_confidence`` + ``signal_value``) and exposes a
``run_backtest`` method (inherited from ``AlgoBase``) that wires the signals
to the engine. EMAs are computed inside the algo from ``close_price`` — no
precomputed analysis columns are required (REQUIRED_COLUMNS is empty).

Public surface (consumed via
``strategy.factors_and_algos.get_algo("macd")`` — returns the ``ALGO``
singleton instance):

  - ALGO                       — singleton MacdAlgo instance
  - fetch_signal_data(conn, sec_type, codes) -> DataFrame
  - apply_signals(df, params) -> DataFrame
  - build_signal_reason(row, side, params, confidence) -> str
  - run_backtest(df, params, sec_type, codes) -> list[dict]   (inherited)
  - compute_daily_rows(...)                                   (inherited)
  - DEFAULT_PARAMS, REQUIRED_COLUMNS, ALGO_PARAM_KEYS, build_params
"""
from __future__ import annotations

from strategy.factors_and_algos.macd.algo import (  # noqa: F401
    MacdAlgo,
    ALGO,
)

# Backward-compat module-level re-exports (delegating to the ALGO singleton).
ALGO_NAME = ALGO.ALGO_NAME
POSITION_AWARE = ALGO.POSITION_AWARE
DEFAULT_PARAMS = ALGO.DEFAULT_PARAMS
REQUIRED_COLUMNS = ALGO.REQUIRED_COLUMNS
ALGO_PARAM_KEYS = ALGO.ALGO_PARAM_KEYS
build_params = ALGO.build_params
apply_signals = ALGO.apply_signals
build_signal_reason = ALGO.build_signal_reason
fetch_signal_data = ALGO.fetch_signal_data
run_backtest = ALGO.run_backtest
compute_daily_rows = ALGO.compute_daily_rows

__all__ = [
    "MacdAlgo",
    "ALGO",
    "ALGO_NAME",
    "POSITION_AWARE",
    "DEFAULT_PARAMS",
    "REQUIRED_COLUMNS",
    "ALGO_PARAM_KEYS",
    "build_params",
    "apply_signals",
    "build_signal_reason",
    "fetch_signal_data",
    "run_backtest",
    "compute_daily_rows",
]
