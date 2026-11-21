"""Algo registry — the single source of truth for all algo configuration.

Adding or removing an algo only requires changes in THIS FILE:
  1. Add the algo's dotted path to ``ALGO_REGISTRY``
  2. Add its abbreviation to ``ALGO_ABBR`` (used in portfolio naming)
  3. (Optionally) change ``DEFAULT_ALGO``

Since the 2026-11-07 signals-picked-up refactor, EVERY analysis signal
family (static.label_dim 'signal_type') is ALSO selectable through
``get_algo`` as a generic pickup algo — no registry entry needed (see
``get_algo``). ``ALGO_REGISTRY`` stays for families that own a dedicated
algo package (macd's reason phrasing).
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
    # The ORACLE mode (2026-11-19): the hindsight local-min/max pivot
    # tier — its own ISOLATED package (analysis.oracle_signals source,
    # swing-proportional confidence; see _oracle/algo.py's docstring).
    # This registry entry is the mode's ONLY footprint outside its
    # package.
    "oracle": "strategy.factors_and_algos._oracle",
}

# ---------------------------------------------------------------------------
#  ALGO_ABBR — name → short abbreviation (for portfolio strategy_name)
#  Keeps the DB strategy_name compact in UI/logs (e.g. "portfolio:macd*0.5").
# ---------------------------------------------------------------------------
ALGO_ABBR: dict[str, str] = {
    "macd": "macd",
    "oracle": "oracle",
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

    Two resolution tiers (signals-picked-up era, 2026-11-07):
      1. ``ALGO_REGISTRY`` — a dedicated algo package (e.g. macd, with
         its own reason phrasing) exposes an ``ALGO`` singleton.
      2. the analysis signal FAMILIES vocabulary (static.label_dim
         'signal_type' labels: mov_rsi / bollinger / ma_cross / ... /
         macd) — a generic :class:`FamilySignalAlgo` pickup instance,
         cached per family. Every family with recorded
         ``analysis_signals.history_signals`` events is selectable
         without a dedicated package.

    Both tiers return a concrete :class:`AlgoBase` subclass instance
    exposing the standard algo surface (fetch_signal_data,
    apply_signals, build_signal_reason, run_backtest, compute_daily_rows,
    build_params, DEFAULT_PARAMS, REQUIRED_COLUMNS, ALGO_PARAM_KEYS,
    ALGO_NAME, POSITION_AWARE).

    Raises ``KeyError`` if the name is neither registered nor a signal
    family. Raises ``AttributeError`` if a registered package does not
    expose an ``ALGO`` singleton.
    """
    if name in ALGO_REGISTRY:
        module = import_module(ALGO_REGISTRY[name])
        return module.ALGO
    # Generic analysis-family tier — the label vocabulary decides.
    from _common.labels import SIGNAL_TYPE_CODE
    if name in SIGNAL_TYPE_CODE:
        from strategy.factors_and_algos._algo.analysis_source import family_algo
        return family_algo(name)
    raise KeyError(
        f"unknown algo '{name}'; registered: {sorted(ALGO_REGISTRY)}; "
        f"analysis signal families: {sorted(SIGNAL_TYPE_CODE)}"
    )


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