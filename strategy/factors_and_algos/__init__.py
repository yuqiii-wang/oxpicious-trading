"""strategy.factors_and_algos — pluggable signal/factor algorithms.

Each sub-package (e.g. ``bollinger_bands``) is a self-contained algo that
produces the two columns the execution engine in ``strategy._trading``
consumes:

  - ``signal_confidence`` ∈ [-100, 100]  (>0 BUY, <0 SELL, 0 none)
  - ``signal_value``                      (auxiliary magnitude)

An algo package declares its own contract — the customizable params it
accepts (``DEFAULT_PARAMS``), the data columns it needs
(``REQUIRED_COLUMNS``), and a ``build_params(overrides)`` seam that merges
defaults with caller-supplied overrides. The strategy package (e.g.
``strategy.singleton_trading``) is the *config source*: it hardcodes (for
now) the overrides + trading-layer params and feeds them to the algo.

Dynamic config
--------------
``load_algo_config`` / ``load_params`` (see loader.py) query
``strategy.algo_configs`` (database/sql/strategy/04_factors_and_algos.sql)
to load per-(security, strategy, date-range) algo param overrides from the
DB. ``load_params`` merges them over the algo's DEFAULT_PARAMS (and any
caller-supplied strategy overrides) — precedence: defaults < DB < overrides.

Registry
--------
``ALGO_REGISTRY`` maps an algo name to its package module. ``get_algo(name)``
returns the algo's ``ALGO`` singleton instance (a concrete
:class:`strategy.factors_and_algos._algo.AlgoBase` subclass), exposing the
standard surface:

  - ``apply_signals(df, params) -> DataFrame``
  - ``build_signal_reason(row, side, params, confidence) -> str``
  - ``run_backtest(df, params, sec_type, codes) -> list[dict]``   (inherited)
  - ``compute_daily_rows(...)``                                    (inherited)
  - ``DEFAULT_PARAMS``, ``REQUIRED_COLUMNS``, ``ALGO_PARAM_KEYS``, ``build_params``
  - ``ALGO_NAME``, ``POSITION_AWARE``

This registry is the seam for future dynamic algo selection (e.g. choosing
an algo by name from a DB row or CLI arg). Registered today:
``bollinger_bands``, ``macd``, ``ma_spread``.

Algo signal collector
---------------------
``AlgoSignalCollector`` (algo_signal_collector.py) sits ABOVE the registry:
it fetches + applies one or more algos and WEIGHTS their signal_confidence
into a single consolidated signal. Today it operates in BINARY mode (one
algo at weight 1.0) but is structured for future MIXED (weighted-blend)
signals.
"""
from __future__ import annotations

from importlib import import_module

from strategy.factors_and_algos._algo import AlgoBase

# Lazy registry: name -> dotted path of the algo package.
# Add new algos here (or register them at runtime via register_algo).
ALGO_REGISTRY: dict[str, str] = {
    "bollinger_bands": "strategy.factors_and_algos.bollinger_bands",
    "macd": "strategy.factors_and_algos.macd",
    "ma_spread": "strategy.factors_and_algos.ma_spread",
}


def register_algo(name: str, dotted_path: str) -> None:
    """Register an algo package by dotted path (extensibility seam)."""
    ALGO_REGISTRY[name] = dotted_path


def get_algo(name: str) -> AlgoBase:
    """Return the algo's ``ALGO`` singleton instance for ``name``.

    Imports the algo package and returns its ``ALGO`` singleton — a concrete
    :class:`AlgoBase` subclass instance exposing the standard algo surface
    (fetch_signal_data, apply_signals, build_signal_reason, run_backtest,
    compute_daily_rows, build_params, DEFAULT_PARAMS, REQUIRED_COLUMNS,
    ALGO_PARAM_KEYS, ALGO_NAME, POSITION_AWARE).

    Raises ``KeyError`` if the algo is not registered. Raises
    ``AttributeError`` if the registered package does not expose an ``ALGO``
    singleton (every algo package's ``__init__`` must define one).
    """
    if name not in ALGO_REGISTRY:
        raise KeyError(
            f"unknown algo '{name}'; registered: {sorted(ALGO_REGISTRY)}"
        )
    module = import_module(ALGO_REGISTRY[name])
    return module.ALGO


# DB-backed config loader. Imported at the bottom of the package init to keep
# the registry/helpers defined first; loader.load_params imports get_algo
# lazily (see loader.py) so there is no circular import at call time.
from strategy.factors_and_algos.loader import (  # noqa: E402,F401
    load_algo_config,
    load_params,
    ensure_default_config,
)

# Algo signal collector — the weighted-consolidation layer above the algos.
from strategy.factors_and_algos.algo_signal_collector import (  # noqa: E402,F401
    AlgoSignalCollector,
)

# Portfolio builder — async sub-algo runs + weight-blended portfolio
# (mixed mode). See portfolio.py.
from strategy.factors_and_algos.portfolio import (  # noqa: E402,F401
    portfolio_name,
    blend_signal_confidence,
    check_position_aware,
    run_sub_algos,
    build_algo_portfolio,
)

__all__ = [
    "ALGO_REGISTRY",
    "register_algo",
    "get_algo",
    "load_algo_config",
    "load_params",
    "ensure_default_config",
    "AlgoSignalCollector",
    "portfolio_name",
    "blend_signal_confidence",
    "check_position_aware",
    "run_sub_algos",
    "build_algo_portfolio",
]
