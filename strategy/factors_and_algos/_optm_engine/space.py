"""Search-space construction — split into two REGIMES, each with its
own optimizer and loss (regime-aware loss splitting, see ``loss/``):

Set A — SIGNAL params (path-independent): the algo's ``TUNABLE_SPACE``
    (e.g. MACD EMA spans / thresholds / weights) PLUS ``conf_threshold``
    (the |signal_confidence| dust filter — a threshold on which signals
    FIRE, so it belongs to the signal regime) — SKIPPED for algos whose
    replay consumes no dust threshold (``AlgoBase.DUST_FILTER`` False —
    the oracle full-fidelity replay). Optimized by Optuna TPE against
    the Omega-Ratio loss (``loss/omega.py``).

Set B — EXECUTION params (path-dependent, multiplicative): the
    trading-layer knobs read by ``AlgoBase.run_backtest`` / the
    execution engine — ``buy_exec_delay`` / ``sell_exec_delay`` (exit
    lags) and ``min_holding_period`` (hold-period offset); the delay
    axes are dropped for mixed mode AND for algos that never shift
    execution dates (``AlgoBase.TUNES_EXEC_DELAYS`` False). Optimized
    by a vanilla grid (default) or a separate TPE study against the
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

# ---------------------------------------------------------------------------
# Set C — comb grid (MIXED mode only; the portfolio's "rules to assign
# weights under different scenarios")
# ---------------------------------------------------------------------------
# A curated, evidence-grounded grid over the _comb stack (skill
# singleton-strategy-tuning Parts 3-12): adaptive family weights ×
# thesis-matched regime gate × vol-state overlay × the blend dust
# threshold. Every axis value is a measured keeper or its null control —
# no open-ended search. quantile_sizing + equity_guard stay ON (both
# measured keepers, Part 6), breadth stays OFF (cross-code feature,
# degenerate for a per-code study).
SET_C_WEIGHT_MODES = ("tstat", "tstat_bar1", "mean_bar", "equal", "ewma")
SET_C_GATE_WINDOWS = (120, 255, 0)  # 0 = regime gate OFF (control)
SET_C_VOL_STATE = (True, False)
# 2026-11-19 validation study (temp_scripts/sig_wfa_validation_study.py):
# the blend conf_threshold measured INERT on every selection segment —
# per-variant thr-neighborhood score deltas sat in the 3rd decimal and the
# run-18 lesson already showed thr neighbors measuring metrically
# identical — so the axis is pinned at the production default 5.0 and the
# Set C grid drops 90 → 30 cells. Re-widen ONLY with new measured evidence.
SET_C_CONF_THRESHOLDS = (5.0,)

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
    values (e.g. a retired span like the pre-2026-10-26 MACD ema10, whose
    column no longer exists → KeyError).

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

    ``conf_threshold`` is SKIPPED for algos that consume no dust
    threshold (``DUST_FILTER`` False — the oracle full-fidelity
    replay): the replay ignores the key entirely, so tuning it would
    burn trials on metrically identical points and store a param the
    run never reads.
    """
    params: dict = {}
    for name, spec in SET_A_EXEC_SPACE.items():
        if name == "conf_threshold" and not getattr(algo, "DUST_FILTER", True):
            continue
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


def set_b_grid(
    mixed: bool = False,
    drop_delays: bool = False,
    drop_min_hp: bool = False,
) -> List[Dict[str, int]]:
    """Vanilla-grid candidates for Set B (Stage B).

    Cartesian product of the stepped SET_B_SPACE ranges in deterministic
    order (288 candidates with the default steps).

    ``mixed=True`` / ``drop_delays=True`` drop the ``buy_exec_delay`` /
    ``sell_exec_delay`` axes: the mixed collector path never applies
    exec delays (only ``AlgoBase.run_backtest`` shifts per-family
    signals — skill Part 3 method notes), and ``drop_delays`` covers
    binary algos whose replay never shifts execution dates
    (``AlgoBase.TUNES_EXEC_DELAYS`` False — the oracle full-fidelity
    replay; the trainer passes it from the backtester's flag).

    ``drop_min_hp`` drops the ``min_holding_period`` axis for binary
    algos whose replay pins it (``AlgoBase.TUNES_MIN_HOLDING`` False —
    ``run_backtest`` overrides the key to 1, so tuning it would grade
    metrically identical points). All three flags together leave ONE
    grid point (an empty product) — the only execution config the
    replay can run. Grading a never-consumed axis burns compute on
    trials whose metrics are exact duplicates (run 18: all 288 points
    of a candidate differed only through ``min_holding_period``). The
    persisted Set-B keys then simply lack the dropped keys — the
    loader's defaults are overridden by the same flags at run time.
    """
    axes = []
    for name, spec in SET_B_SPACE.items():
        if (mixed or drop_delays) and name in ("buy_exec_delay", "sell_exec_delay"):
            continue
        if drop_min_hp and name == "min_holding_period":
            continue
        step = SET_B_GRID_STEPS.get(name, 1)
        lo, hi = int(spec["low"]), int(spec["high"])
        axes.append([(name, v) for v in range(lo, hi + 1, step)])
    return [dict(combo) for combo in itertools.product(*axes)]


def set_c_comb_grid(has_reject_rows: bool = False,
                    has_no_action_rows: bool = False) -> List[dict]:
    """Curated comb-grid candidates for Set C (mixed-mode Stage C).

    One cell per (weight mode × gate window × vol_state ×
    conf_threshold) — 5 × 3 × 2 × 3 = 90 cells, each carrying a full
    ``CombParams.model_dump()`` under ``"comb"`` plus the blend
    ``conf_threshold``. Graded by ``evaluate_set_c`` (Calmar on IS +
    official risk tiebreak); the winning cell rides the candidate params
    through Kelly / Set B / final selection unchanged.

    ``has_reject_rows`` — the fetched frames carry reject-tier evidence
    (any non-null ``rej_action``; computed by the trainer from the
    collector's per-algo frames). When True, every cell gets a
    reject-ON twin: the family-local same-side self-suppression
    (``_comb/reject.py``) joins the trained space — the reject
    OFFSETTING becomes optimizable exactly when there is reject
    evidence to optimize on, under the SAME preference/DD-cap/OOS
    funnel as every other axis (a reject-ON winner must beat its
    reject-OFF twin through that funnel — the Part 2 D acceptance bar,
    structural). When False (the tier's birth-probation soft-launch
    state, 0 rows live) the grid is UNCHANGED — the axis is simply
    absent rather than pinned: no reject evidence, nothing to tune.
    CROSS-family reject offsets are not and will not be a grid axis
    (measured harmful — skill singleton-strategy-tuning Part 16).

    ``has_no_action_rows`` — the same availability gate for the
    NO-ACTION overlay marks (any non-null ``na_action``): when True
    every cell additionally gets a no-action-ON twin (the family-local
    suppression of MARKED FIRED days, ``_comb/no_action.py`` — the
    reject tier's day-level mirror, same funnel, same ON-must-beat-OFF
    bar). With both evidences present the grid quadruples (90 → 360
    cells); the structural-dedupe key in ``optimize_set_c`` keeps the
    variant count manageable.
    """
    from strategy.factors_and_algos._comb.config import (
        AdhocActionsCfg,
        CombParams,
        EquityGuardCfg,
        NoActionCfg,
        QuantileCfg,
        RegimeGateCfg,
        RejectCfg,
        ToggleCfg,
        WeightsCfg,
    )

    cells: List[dict] = []
    for mode in SET_C_WEIGHT_MODES:
        for gate_win in SET_C_GATE_WINDOWS:
            for vol in SET_C_VOL_STATE:
                for rej in ((False, True) if has_reject_rows
                            else (False,)):
                    for na in ((False, True) if has_no_action_rows
                               else (False,)):
                        comb = CombParams(
                            weights=WeightsCfg(mode=mode),
                            regime_gate=RegimeGateCfg(
                                enabled=gate_win > 0,
                                window=gate_win or 120,
                            ),
                            quantile_sizing=QuantileCfg(enabled=True),
                            vol_state=ToggleCfg(enabled=vol),
                            equity_guard=EquityGuardCfg(enabled=True),
                            breadth=ToggleCfg(enabled=False),
                            # Ad-hoc actions are pinned OFF in the trainer:
                            # the training landscape stays frozen at the
                            # measured Part 10-14 stack until the trim
                            # module beats the no-overlay OOS Calmar bar
                            # (skill Part 2 D).
                            adhoc_actions=AdhocActionsCfg(enabled=False),
                            reject=RejectCfg(enabled=rej),
                            no_action=NoActionCfg(enabled=na),
                        )
                        for thr in SET_C_CONF_THRESHOLDS:
                            cells.append({
                                "comb": comb.model_dump(),
                                "conf_threshold": thr,
                            })
    return cells


__all__ = [
    "SET_A_EXEC_SPACE",
    "SET_B_SPACE",
    "SET_B_DEFAULTS",
    "SET_B_GRID_STEPS",
    "SET_C_WEIGHT_MODES",
    "SET_C_GATE_WINDOWS",
    "SET_C_VOL_STATE",
    "SET_C_CONF_THRESHOLDS",
    "COMMON_SPACE",
    "suggest_set_a_params",
    "suggest_set_b_params",
    "set_b_grid",
    "set_c_comb_grid",
    "snap_categorical_params",
]
