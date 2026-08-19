"""Algo registry — the single source of truth for all algo configuration.

Adding or removing an algo only requires changes in THIS FILE:
  1. Add the algo's dotted path to ``ALGO_REGISTRY``
  2. Add its abbreviation to ``ALGO_ABBR`` (used in portfolio naming)
  3. (Optionally) change ``DEFAULT_ALGO``

Everything else in the codebase imports from here.
"""
from __future__ import annotations

from importlib import import_module

from strategy.factors_and_algos._algo.base import AlgoBase

# ---------------------------------------------------------------------------
#  ALGO_REGISTRY — name → dotted path of the algo package
#  Each algo package (e.g. strategy.factors_and_algos.macd) must expose
#  an ``ALGO`` singleton instance in its __init__.py.
# ---------------------------------------------------------------------------
ALGO_REGISTRY: dict[str, str] = {
    "macd": "strategy.factors_and_algos.macd",
}

# ---------------------------------------------------------------------------
#  ALGO_ABBR — name → short abbreviation (for portfolio strategy_name)
#  Keeps the DB strategy_name compact in UI/logs (e.g. "portfolio:macd*0.5").
# ---------------------------------------------------------------------------
ALGO_ABBR: dict[str, str] = {
    "macd": "macd",
}

# ---------------------------------------------------------------------------
#  DEFAULT_ALGO — the algo used when --algo is omitted from the CLI
# ---------------------------------------------------------------------------
DEFAULT_ALGO: str = "macd"


def register_algo(name: str, dotted_path: str) -> None:
    """Register an algo package by dotted path (runtime extensibility seam)."""
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


def short_name(algo_name: str) -> str:
    """Abbreviate an algo name for the portfolio strategy_name (readability).

    The DB ``strategy_name`` column is TEXT, so the full name would work too,
    but abbreviations keep the name compact in UI/logs.
    """
    return ALGO_ABBR.get(algo_name, algo_name)


__all__ = [
    "ALGO_REGISTRY",
    "ALGO_ABBR",
    "DEFAULT_ALGO",
    "register_algo",
    "get_algo",
    "short_name",
]