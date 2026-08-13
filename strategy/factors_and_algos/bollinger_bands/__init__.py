"""strategy.factors_and_algos.bollinger_bands — Bollinger Band (MA20+MA60) mean-reversion.

A pluggable signal algo implementing :class:`strategy.factors_and_algos._algo.AlgoBase`.
Produces the two columns the execution engine in ``strategy._trading``
consumes (``signal_confidence`` + ``signal_value``) and exposes a
``run_backtest`` method (inherited from ``AlgoBase``) that wires the signals
to the engine.

Public surface (consumed via
``strategy.factors_and_algos.get_algo("bollinger_bands")`` — returns the
``ALGO`` singleton instance):

  - ALGO                       — singleton BollingerBandsAlgo instance
  - fetch_signal_data(conn, sec_type, codes) -> DataFrame
  - apply_signals(df, params) -> DataFrame
  - build_signal_reason(row, side, params, confidence) -> str
  - run_backtest(df, params, sec_type, codes) -> list[dict]   (inherited)
  - compute_daily_rows(...)                                   (inherited)
  - DEFAULT_PARAMS, REQUIRED_COLUMNS, ALGO_PARAM_KEYS, build_params
"""
from __future__ import annotations

from strategy.factors_and_algos.bollinger_bands.algo import (  # noqa: F401
    BollingerBandsAlgo,
    ALGO,
    _z_score,
)

# Backward-compat module-level re-exports (delegating to the ALGO singleton).
# Callers that do ``algo.fetch_signal_data(...)`` / ``algo.apply_signals(...)``
# / ``algo.ALGO_PARAM_KEYS`` etc. work unchanged whether ``algo`` is the
# module or the singleton instance.
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
    "BollingerBandsAlgo",
    "ALGO",
    "ALGO_NAME",
    "POSITION_AWARE",
    "DEFAULT_PARAMS",
    "REQUIRED_COLUMNS",
    "ALGO_PARAM_KEYS",
    "build_params",
    "_z_score",
    "apply_signals",
    "build_signal_reason",
    "fetch_signal_data",
    "run_backtest",
    "compute_daily_rows",
]
