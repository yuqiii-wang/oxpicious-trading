"""strategy.factors_and_algos.macd — MACD crossover via analysis signals.

A pluggable signal algo implementing
:class:`strategy.factors_and_algos._algo.AnalysisSignalAlgoBase`: the
signals are PICKED UP from ``analysis_signals.history_signals``
(signal_type = 10 — the analysis tier's macd family, the classic
12/26/9 signal-line crossover), never recomputed here. The generic
fetch (OHLC + the family's standalone-tier events) and the generic
event→``signal_confidence`` mapping are inherited; the algo contributes
only its family identity and the reason phrasing.

Public surface (consumed via
``strategy.factors_and_algos.get_algo("macd")`` — returns the ``ALGO``
singleton instance):

  - ALGO                       — singleton MacdAlgo instance
  - fetch_signal_data(conn, sec_type, codes) -> DataFrame   (inherited)
  - apply_signals(df, params) -> DataFrame                  (inherited)
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
