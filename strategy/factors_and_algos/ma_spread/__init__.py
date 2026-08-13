"""strategy.factors_and_algos.ma_spread — MA5/MA{long} cross trend-following.

A pluggable signal algo implementing :class:`strategy.factors_and_algos._algo.AlgoBase`.
Produces the two columns the execution engine in ``strategy._trading`` consumes
(``signal_confidence`` + ``signal_value``), owns its own DB fetch
(mov_ave_spreads_detail + mov_ave_rsi + basic_stats), and exposes a
``run_backtest`` method (inherited from ``AlgoBase``) that wires the signals
to the engine.

Public surface (consumed via
``strategy.factors_and_algos.get_algo("ma_spread")`` — returns the ``ALGO``
singleton instance):

  - ALGO                       — singleton MaSpreadAlgo instance
  - fetch_signal_data(conn, sec_type, codes) -> DataFrame
  - apply_signals(df, params) -> DataFrame
  - build_signal_reason(row, side, params, confidence) -> str
  - run_backtest(df, params, sec_type, codes) -> list[dict]   (inherited)
  - compute_daily_rows(...)                                   (inherited)
  - DEFAULT_PARAMS, REQUIRED_COLUMNS, ALGO_PARAM_KEYS, build_params
  - DETAIL_SIGNAL_COLUMNS, RSI_SIGNAL_COLUMNS                 (ma_spread-specific)
"""
from __future__ import annotations

from strategy.factors_and_algos.ma_spread.algo import (  # noqa: F401
    MaSpreadAlgo,
    ALGO,
    _clip01_series,
    _add_cross_columns,
    _mark_entry_signals,
    _mark_exit_signals,
    _add_confidence_columns,
    _consolidate_signal,
)

# Backward-compat module-level re-exports (delegating to the ALGO singleton).
ALGO_NAME = ALGO.ALGO_NAME
POSITION_AWARE = ALGO.POSITION_AWARE
DEFAULT_PARAMS = ALGO.DEFAULT_PARAMS
DETAIL_SIGNAL_COLUMNS = ALGO.DETAIL_SIGNAL_COLUMNS
RSI_SIGNAL_COLUMNS = ALGO.RSI_SIGNAL_COLUMNS
REQUIRED_COLUMNS = ALGO.REQUIRED_COLUMNS
ALGO_PARAM_KEYS = ALGO.ALGO_PARAM_KEYS
build_params = ALGO.build_params
apply_signals = ALGO.apply_signals
build_signal_reason = ALGO.build_signal_reason
fetch_signal_data = ALGO.fetch_signal_data
run_backtest = ALGO.run_backtest
compute_daily_rows = ALGO.compute_daily_rows

__all__ = [
    "MaSpreadAlgo",
    "ALGO",
    "ALGO_NAME",
    "POSITION_AWARE",
    "DEFAULT_PARAMS",
    "DETAIL_SIGNAL_COLUMNS",
    "RSI_SIGNAL_COLUMNS",
    "REQUIRED_COLUMNS",
    "ALGO_PARAM_KEYS",
    "build_params",
    "_clip01_series",
    "_add_cross_columns",
    "_mark_entry_signals",
    "_mark_exit_signals",
    "_add_confidence_columns",
    "_consolidate_signal",
    "apply_signals",
    "build_signal_reason",
    "fetch_signal_data",
    "run_backtest",
    "compute_daily_rows",
]
