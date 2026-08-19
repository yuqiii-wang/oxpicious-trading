"""Search-space construction — split into two REGIMES, each with its
own optimizer and loss (regime-aware loss splitting, see ``loss/``):

Set A — SIGNAL params (path-independent): the algo's ``TUNABLE_SPACE``
    (e.g. MACD EMA spans / thresholds / weights) PLUS ``conf_threshold``
    (the |signal_confidence| dust filter — a threshold on which signals
    FIRE, so it belongs to the signal regime). Optimized by Optuna TPE
    against the Omega-Ratio loss (``loss/omega.py``).

Set B — EXECUTION params (path-dependent, multiplicative): the
    trading-layer knobs read by ``AlgoBase.run_backtest`` / the
    execution engine — ``buy_exec_delay`` / ``sell_exec_delay`` (exit
    lags) and ``min_holding_period`` (hold-period offset). Optimized by
    a vanilla grid (default) or a separate TPE study against the
    Calmar loss (``loss/calmar.py``).

``COMMON_SPACE`` (kept for documentation / backward compatibility) is
the union of the Set A conf_threshold spec and the Set B keys.
"""
from __future__ import annotations

import itertools
from typing import Any, Dict, List

from strategy.factors_and_algos._algo import AlgoBase


# ---------------------------------------------------------------------------
#  Set A — signal regime (TPE + Omega loss)
# ---------------------------------------------------------------------------
#  conf_threshold — minimum |signal_confidence| to fire a trade
#                   (default 5.0, see _algo/tuning.py)
SET_A_EXEC_SPACE: Dict[str, dict] = {
    "conf_threshold": {"type": "float", "low": 1.0, "high": 30.0},
}


# ---------------------------------------------------------------------------
#  Set B — execution regime (grid / separate TPE + Calmar loss)
# ---------------------------------------------------------------------------
#  buy_exec_delay     — trading days between BUY signal bar and execution
#                       ("what date to buy")
#  sell_exec_delay    — trading days between SELL signal bar and execution
#                       ("what date to sell")
#  min_holding_period — engine gate: SELLs allowed only N trading days
#                       after the last BUY (strategy default 5)
SET_B_SPACE: Dict[str, dict] = {
    "buy_exec_delay": {"type": "int", "low": 0, "high": 5},
    "sell_exec_delay": {"type": "int", "low": 0, "high": 5},
    "min_holding_period": {"type": "int", "low": 1, "high": 15},
}

# Stage-A evaluation defaults for the Set B keys (the strategy defaults
# — singleton_trading STRATEGY_PARAMS). During Stage A only keys NOT
# already fixed via --params-json (base_params) are filled in, so a
# user-fixed execution param stays fixed.
SET_B_DEFAULTS: Dict[str, Any] = {
    "buy_exec_delay": 0,
    "sell_exec_delay": 0,
    "min_holding_period": 5,
}

# Vanilla-grid coarseness for Set B (Stage B): cartesian product of the
# stepped ranges → 6 × 6 × 8 = 288 candidates.
SET_B_GRID_STEPS: Dict[str, int] = {
    "buy_exec_delay": 1,
    "sell_exec_delay": 1,
    "min_holding_period": 2,
}

# Union kept for documentation / backward compatibility.
COMMON_SPACE: Dict[str, dict] = {**SET_A_EXEC_SPACE, **SET_B_SPACE}


def _suggest_one(trial, name: str, spec: dict) -> Any:
    """Dispatch one space entry to the matching Optuna suggest call."""
    kind = spec.get("type")
    if kind == "int":
        return trial.suggest_int(
            name, int(spec["low"]), int(spec["high"]),
            step=int(spec["step"]) if spec.get("step") else 1,
            log=bool(spec.get("log", False)),
        )
    if kind == "float":
        return trial.suggest_float(
            name, float(spec["low"]), float(spec["high"]),
            step=float(spec["step"]) if spec.get("step") else None,
            log=bool(spec.get("log", False)),
        )
    if kind == "categorical":
        return trial.suggest_categorical(name, spec["choices"])
    raise ValueError(f"unknown space entry type '{kind}' for '{name}'")


def _repair_params(params: dict, algo: AlgoBase) -> dict:
    """Deterministically repair known-invalid param combos.

    MACD-style EMAs: ``ema_short`` must be strictly smaller than
    ``ema_long``. When Optuna samples an invalid pair (e.g. 60/60), bump
    ``ema_long`` to the smallest valid choice greater than ``ema_short``;
    if none exists, fall back to the algo defaults.
    """
    if "ema_short" in params and "ema_long" in params:
        short, long = int(params["ema_short"]), int(params["ema_long"])
        if short >= long:
            space = getattr(algo, "TUNABLE_SPACE", {}).get("ema_long", {})
            choices = sorted(
                c for c in space.get("choices", []) if int(c) > short
            )
            if choices:
                params["ema_long"] = choices[0]
            else:
                params["ema_short"] = algo.DEFAULT_PARAMS["ema_short"]
                params["ema_long"] = algo.DEFAULT_PARAMS["ema_long"]
    return params


def snap_categorical_params(algo: AlgoBase, params: dict) -> dict:
    """Enforce DISCRETE options on every categorical TUNABLE_SPACE param.

    Some params can only take discrete values (e.g. MACD's ``ema_short`` /
    ``ema_long`` map 1:1 to precomputed ``ema{N}`` DB columns). Optuna's
    ``suggest_categorical`` already samples only legal choices, but params
    can ALSO arrive from outside sources — stale ``algo_configs`` rows,
    hand-written ``--params-json``, repaired fallbacks — carrying illegal
    values (e.g. classic MACD 12/26 with no ema12/ema26 columns → KeyError).

    This snaps any out-of-choices numeric value to the NEAREST legal
    choice (ties → smaller), in place. Runs after :func:`_repair_params`
    so repaired values are also normalized.
    """
    space = getattr(algo, "TUNABLE_SPACE", {}) or {}
    for name, spec in space.items():
        if spec.get("type") != "categorical":
            continue
        value = params.get(name)
        if value is None:
            continue
        choices = spec.get("choices") or []
        if not choices or value in choices:
            continue
        try:
            v = float(value)
            best = min(choices, key=lambda c: (abs(float(c) - v), float(c)))
            params[name] = type(choices[0])(best) if all(
                isinstance(c, int) for c in choices
            ) else best
        except (TypeError, ValueError):
            # Non-numeric choices — fall back to the first legal option
            # rather than crash the study.
            params[name] = choices[0]
    return params


def suggest_set_a_params(trial, algo: AlgoBase) -> dict:
    """Sample one trial's Set A (signal regime) params.

    conf_threshold + the algo's TUNABLE_SPACE. Discrete (categorical)
    params are sampled from their declared choices ONLY and re-snapped
    after repair so every emitted param set is legal.
    """
    params: dict = {}
    for name, spec in SET_A_EXEC_SPACE.items():
        params[name] = _suggest_one(trial, name, spec)
    for name, spec in (getattr(algo, "TUNABLE_SPACE", {}) or {}).items():
        params[name] = _suggest_one(trial, name, spec)
    params = _repair_params(params, algo)
    return snap_categorical_params(algo, params)


def suggest_set_b_params(trial) -> dict:
    """Sample one trial's Set B (execution regime) params."""
    params: dict = {}
    for name, spec in SET_B_SPACE.items():
        params[name] = _suggest_one(trial, name, spec)
    return params


def set_b_grid() -> List[Dict[str, int]]:
    """Vanilla-grid candidates for Set B (Stage B).

    Cartesian product of the stepped SET_B_SPACE ranges in
    deterministic order (288 candidates with the default steps).
    """
    axes = []
    for name, spec in SET_B_SPACE.items():
        step = SET_B_GRID_STEPS.get(name, 1)
        lo, hi = int(spec["low"]), int(spec["high"])
        axes.append([(name, v) for v in range(lo, hi + 1, step)])
    return [dict(combo) for combo in itertools.product(*axes)]


__all__ = [
    "SET_A_EXEC_SPACE",
    "SET_B_SPACE",
    "SET_B_DEFAULTS",
    "SET_B_GRID_STEPS",
    "COMMON_SPACE",
    "suggest_set_a_params",
    "suggest_set_b_params",
    "set_b_grid",
    "snap_categorical_params",
]
