"""strategy.factors_and_algos._algo — shared algo infrastructure + registry.

Contains:
  - The abstract base class (:class:`AlgoBase`) that defines the
    pluggable-algo contract
  - Shared fetch helpers (``basic_stats_table``, ``tech_stats_table``,
    ``rows_to_df``)
  - Signal tuning helpers (``tune_signals``)
  - **Algo registry** (``registry.py``) — the single source of truth for
    ALGO_REGISTRY, ALGO_ABBR, DEFAULT_ALGO, get_algo, short_name, etc.

Every concrete algo package
(``macd``) defines a subclass of
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
    tech_stats_table,
    rows_to_df,
)
from strategy.factors_and_algos._algo.tuning import (  # noqa: F401
    SIGNAL_CONFIDENCE_THRESHOLD,
    tune_signals,
    apply_exec_delays,
)
from strategy.factors_and_algos._algo.registry import (  # noqa: F401
    ALGO_REGISTRY,
    ALGO_ABBR,
    DEFAULT_ALGO,
    register_algo,
    get_algo,
    short_name,
)

__all__ = [
    "AlgoBase",
    "basic_stats_table",
    "tech_stats_table",
    "rows_to_df",
    "SIGNAL_CONFIDENCE_THRESHOLD",
    "tune_signals",
    "apply_exec_delays",
    "ALGO_REGISTRY",
    "ALGO_ABBR",
    "DEFAULT_ALGO",
    "register_algo",
    "get_algo",
    "short_name",
]