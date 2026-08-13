"""strategy.factors_and_algos._algo — shared algo infrastructure.

Contains the abstract base class (:class:`AlgoBase`) that defines the
pluggable-algo contract, plus shared fetch helpers (moved from the former
``_fetch_util.py``). Every concrete algo package
(``bollinger_bands``, ``macd``, ``ma_spread``) defines a subclass of
``AlgoBase`` and exposes a singleton instance ``ALGO`` in its ``__init__``.

The ABC enforces a CONSISTENT surface across algos:

  - Class attrs: ``ALGO_NAME``, ``POSITION_AWARE``, ``DEFAULT_PARAMS``,
    ``REQUIRED_COLUMNS``, ``ALGO_PARAM_KEYS``
  - Abstract methods (each algo implements):
      ``fetch_signal_data``, ``apply_signals``, ``build_signal_reason``
  - Concrete methods (shared, inherited): ``build_params``, ``run_backtest``,
    ``compute_daily_rows``
"""
from __future__ import annotations

from strategy.factors_and_algos._algo.base import AlgoBase  # noqa: F401
from strategy.factors_and_algos._algo.fetch_base import (  # noqa: F401
    basic_stats_table,
    rows_to_df,
)

__all__ = ["AlgoBase", "basic_stats_table", "rows_to_df"]
