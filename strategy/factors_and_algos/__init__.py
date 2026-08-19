"""strategy.factors_and_algos — pluggable signal/factor algorithms.

Each sub-package (e.g. ``macd``) is a self-contained algo that
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

Registry (single source: strategy.factors_and_algos._algo.registry)
-------------------------------------------------------------------
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

To add/remove algos, edit ``_algo/registry.py`` only. Registered today:
``macd``.

Algo signal collector
---------------------
``AlgoSignalCollector`` (algo_signal_collector.py) sits ABOVE the registry:
it fetches + applies one or more algos and WEIGHTS their signal_confidence
into a single consolidated signal. Today it operates in BINARY mode (one
algo at weight 1.0) but is structured for future MIXED (weighted-blend)
signals.
"""
from __future__ import annotations

# IMPORTANT: this package init is deliberately LAZY (PEP 562 __getattr__).
#
# ``python -m strategy.factors_and_algos._optm_engine`` executes THIS
# __init__ before ``_optm_engine/__main__.py`` gets to run. The old eager
# imports (registry → macd algo → pandas) meant pandas was already in
# sys.modules before the GPU decision, so cudf.pandas could never patch
# the pandas import. Keep this module pandas-free at import time.

__all__ = [
    "ALGO_REGISTRY",
    "ALGO_ABBR",
    "DEFAULT_ALGO",
    "register_algo",
    "get_algo",
    "short_name",
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

_REGISTRY_NAMES = {"ALGO_REGISTRY", "ALGO_ABBR", "DEFAULT_ALGO",
                   "register_algo", "get_algo", "short_name"}
_LOADER_NAMES = {"load_algo_config", "load_params", "ensure_default_config"}
_COLLECTOR_NAMES = {"AlgoSignalCollector"}
_PORTFOLIO_NAMES = {"portfolio_name", "blend_signal_confidence",
                    "check_position_aware", "run_sub_algos",
                    "build_algo_portfolio"}


def __getattr__(name: str):
    if name in _REGISTRY_NAMES:
        from strategy.factors_and_algos._algo import registry
        return getattr(registry, name)
    if name in _LOADER_NAMES:
        from strategy.factors_and_algos import loader
        return getattr(loader, name)
    if name in _COLLECTOR_NAMES:
        from strategy.factors_and_algos import algo_signal_collector
        return getattr(algo_signal_collector, name)
    if name in _PORTFOLIO_NAMES:
        from strategy.factors_and_algos import portfolio
        return getattr(portfolio, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))