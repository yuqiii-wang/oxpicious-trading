"""Regime-aware two-stage optimization objective (loss design in ``loss/``).

The two parameter regimes get DIFFERENT losses — applying one objective
to both ruins the optimization:

Stage A — Set A signal params (``conf_threshold`` + algo
    ``TUNABLE_SPACE``): path-independent signal generation → Omega
    Ratio of the per-exit return rates on the IS segment, hard-
    constrained to > 55% positive monthly PnL (``loss/omega.py``).
    Optimized with Optuna TPE.

Stage B — Set B execution params (``buy_exec_delay`` /
    ``sell_exec_delay`` / ``min_holding_period``): path-dependent
    compounding → Calmar Ratio (total return / max drawdown) on the
    OOS segment, hard-constrained to max drawdown ≤ 25% of peak
    capital (``loss/calmar.py``). Optimized with a vanilla grid (or a
    separate TPE study).

Both stages backtest purely in-memory against fetch-once cached
DataFrames (``dfs`` = IS split for Stage A, ``oos_dfs`` = OOS split
for Stage B; with ``--oos-frac 0`` both point at the full series).
Every trial forces ``skip_final_liquidation=False`` so PnL includes
the mark-out of the open position — clean and comparable across
trials. No DB rows are written during the study.
"""
from __future__ import annotations

from typing import Any, Dict, List

import pandas as pd

from strategy._trading.constants import DEFAULT_FAULT_TOLERANCE
from strategy._trading.engine import compute_total_buy_cost
from strategy.factors_and_algos._optm_engine.backtester import OptmBacktester
from strategy.factors_and_algos._optm_engine.loss import (
    CALMAR_LOSS,
    LossEvaluation,
    OMEGA_LOSS,
)
from strategy.factors_and_algos._optm_engine.loss.metrics import (
    aggregate_monthly_pnl,
    equity_max_drawdown,
    monthly_pnl,
    monthly_pnl_from_decisions,
    trade_returns,
)


class OptmContext:
    """Immutable per-study context: backtester + fetch-once data + statics.

    ``backtester`` — the evaluation adapter (``AlgoBacktester`` for a
                  single algo / binary mode, ``CollectorBacktester`` for
                  a mixed-family portfolio; see ``backtester.py``).
    ``is_mixed``  — True in mixed mode: the trainer replaces Stage-A TPE
                  with the curated Set C comb grid (the portfolio's
                  weight rules), see ``trainer.py``.
    ``dfs``     — Stage A evaluation set (IS split, or the full series
                  when ``--oos-frac 0``). Maps code → DataFrame slice
                  with all model columns present (e.g. MACD's
                  ema6/12/20/26/60/120/252), fetched ONE time before the study.
    ``oos_dfs`` — Stage B evaluation set (OOS split, or the full series).
    ``statics`` carries the fixed execution assumptions (fee_rate,
    slippage_band, buy_notional, fault_tolerance) merged into every
    trial's params; ``base_params`` carries ``--params-json`` overrides.
    """

    def __init__(
        self,
        backtester: OptmBacktester,
        sec_type: str,
        codes: List[str],
        dfs: Dict[str, pd.DataFrame],
        oos_dfs: Dict[str, pd.DataFrame],
        statics: Dict[str, Any],
        base_params: Dict[str, Any] | None = None,
        is_mixed: bool = False,
    ):
        self.backtester = backtester
        self.sec_type = sec_type
        self.codes = list(codes)
        self.dfs = dfs
        self.oos_dfs = oos_dfs
        self.statics = dict(statics)
        self.base_params = dict(base_params or {})
        self.is_mixed = is_mixed


def _merge_params(ctx: OptmContext, overrides: Dict[str, Any]) -> Dict[str, Any]:
    """base_params < overrides < statics + forced execution settings."""
    params: Dict[str, Any] = dict(ctx.base_params)
    params.update(overrides)
    params.update(ctx.statics)
    # Discrete-option enforcement: snap any categorical TUNABLE_SPACE
    # param that arrived from a non-sampler source (base_params
    # --params-json / stale DB values) to its nearest legal choice.
    from strategy.factors_and_algos._optm_engine.space import (
        snap_categorical_params,
    )
    params = snap_categorical_params(ctx.backtester, params)
    # Optimization-only execution settings: force the final liquidation
    # so every trial's PnL includes the mark-out of the open position.
    params["skip_final_liquidation"] = False
    # Studies evaluate at the pinned production fault tolerance (the
    # engine's BUY-fill stress) — statics normally carry it already.
    params.setdefault("fault_tolerance", DEFAULT_FAULT_TOLERANCE)
    return params


def _run_backtests(
    ctx: OptmContext, dfs: Dict[str, pd.DataFrame], params: Dict[str, Any],
    segment: str = "is",
) -> Dict[str, dict]:
    """Run one param set across all codes on an evaluation set.

    ``segment`` ("is" / "oos" / "full") prepares the backtester before
    the loop — the mixed collector swaps its per-algo frames to the
    segment's sliced copies; the binary adapter is a no-op.

    Returns per-code ``{"decisions", "daily_rows", "pnl", "capital"}``.
    Decisions are sorted + numbered (the daily-rows helper expects it);
    ``pnl`` = Σ SELL realized_pnl (incl. the final-liquidation SELL);
    ``capital`` = peak capital deployed (compute_total_buy_cost).
    """
    ctx.backtester.prepare(segment)
    per_code: Dict[str, dict] = {}
    for code in ctx.codes:
        df = dfs.get(code)
        if df is None or df.empty:
            per_code[code] = {"decisions": [], "daily_rows": [],
                              "pnl": 0.0, "capital": 0.0}
            continue

        decisions = ctx.backtester.run_backtest(
            df, params, ctx.sec_type, [code],
        )
        decisions.sort(key=lambda d: d["exec_date"])
        for i, d in enumerate(decisions, start=1):
            d["decision_no"] = i

        if not decisions:
            per_code[code] = {"decisions": [], "daily_rows": [],
                              "pnl": 0.0, "capital": 0.0}
            continue

        # Daily rows for the equity curve (monthly PnL / max drawdown).
        first_buy = next(
            (d for d in decisions if d["side"] == "BUY"), None,
        )
        anchor = float(first_buy["fill_price"]) if first_buy else None
        daily_rows: List[dict] = []
        if anchor is not None:
            daily_rows = ctx.backtester.compute_daily_rows(
                df, decisions, anchor,
            )

        pnl = sum(
            float(d.get("realized_pnl") or 0.0)
            for d in decisions if d["side"] == "SELL"
        )
        capital = compute_total_buy_cost(decisions)

        per_code[code] = {
            "decisions": decisions,
            "daily_rows": daily_rows,
            "pnl": pnl,
            "capital": capital,
        }
    return per_code


def _aggregate(per_code: Dict[str, dict]) -> dict:
    """Pool per-code backtest outputs into cross-code metric inputs."""
    returns: List[float] = []
    monthlies: List[Dict[str, float]] = []
    for res in per_code.values():
        returns.extend(trade_returns(res["decisions"]))
        m = monthly_pnl(res["daily_rows"]) or monthly_pnl_from_decisions(
            res["decisions"],
        )
        monthlies.append(m)
    return {
        "returns": returns,
        "monthly": aggregate_monthly_pnl(monthlies),
        "dd": equity_max_drawdown(
            [res["daily_rows"] for res in per_code.values()],
        ),
        "total_pnl": sum(res["pnl"] for res in per_code.values()),
        "total_capital": sum(res["capital"] for res in per_code.values()),
        "n_decisions": sum(len(res["decisions"]) for res in per_code.values()),
    }


def _per_code_summary(per_code: Dict[str, dict]) -> Dict[str, dict]:
    return {
        c: {"pnl": round(r["pnl"], 4), "capital": round(r["capital"], 4),
            "n_decisions": len(r["decisions"])}
        for c, r in per_code.items()
    }


def evaluate_set_a(ctx: OptmContext, set_a_params: dict) -> dict:
    """Stage A: Omega of the raw signal returns, Set B at its defaults.

    ``set_a_params`` carries conf_threshold + the algo's model params.
    The Set B execution keys are held at the strategy defaults (or the
    user-fixed ``--params-json`` values) — signal quality must not be
    graded through an execution path that is not being tuned yet.
    Evaluated on the IS set (``ctx.dfs``).
    """
    from strategy.factors_and_algos._optm_engine.space import SET_B_DEFAULTS

    set_b = {k: v for k, v in SET_B_DEFAULTS.items()
             if k not in ctx.base_params}
    params = _merge_params(ctx, {**set_a_params, **set_b})

    per_code = _run_backtests(ctx, ctx.dfs, params, segment="is")
    agg = _aggregate(per_code)

    bundle = OMEGA_LOSS.evaluate(LossEvaluation.from_aggregate(agg))
    bundle.update({
        "pnl": round(agg["total_pnl"], 4),
        "capital": round(agg["total_capital"], 4),
        "n_decisions": agg["n_decisions"],
        "per_code": _per_code_summary(per_code),
    })
    return bundle


def evaluate_set_b(
    ctx: OptmContext, set_b_params: dict, set_a_params: dict,
) -> dict:
    """Stage B: Calmar on the OOS segment, Set A frozen at Stage A's best.

    ``set_a_params`` is the winning Stage A param set (signal regime
    fixed); ``set_b_params`` is the execution candidate being graded.
    Evaluated on the OOS set (``ctx.oos_dfs``).
    """
    params = _merge_params(ctx, {**set_a_params, **set_b_params})

    per_code = _run_backtests(ctx, ctx.oos_dfs, params, segment="oos")
    agg = _aggregate(per_code)
    ev = LossEvaluation.from_aggregate(agg)

    # total_return / max_dd_pct come from the loss bundle's context
    # fields; here we only add execution detail the loss doesn't know.
    bundle = CALMAR_LOSS.evaluate(ev)
    # Official risk score (skill Part 9): computed only for points that
    # trade AND pass the DD cap — it is the tiebreaker in final
    # selection, never a reason to keep a breaching point.
    bundle["risk_score"] = None
    if not bundle.get("no_trades") and not bundle.get("violation"):
        from strategy.factors_and_algos._optm_engine.risk_score import (
            official_risk_score,
        )
        bundle["risk_score"] = official_risk_score(per_code, ctx.oos_dfs)
    bundle.update({
        "pnl": round(agg["total_pnl"], 4),
        "capital": round(ev.total_capital, 4),
        "max_dd_abs": round(ev.max_dd_abs, 6),
        "dd_trough_date": agg["dd"].get("trough_date"),
        "n_decisions": agg["n_decisions"],
        "per_code": _per_code_summary(per_code),
    })
    return bundle


def evaluate_set_c(ctx: OptmContext, set_c_params: dict) -> dict:
    """Stage C (mixed mode only): Calmar on IS over one curated comb cell.

    ``set_c_params`` carries ``{"comb": <CombParams model_dump>,
    "conf_threshold": x}`` — one cell of the Set C comb grid
    (``space.set_c_comb_grid``). Set B keys are held at their neutral
    defaults. Calmar (not Omega) is the grading loss: the comb study
    (skill Part 3) measured that combs must be judged on Calmar/DD +
    the official risk score, never on omega alone. The winner's comb
    block then rides ``set_a_params`` through Set B / final selection.
    """
    from strategy.factors_and_algos._optm_engine.space import SET_B_DEFAULTS

    set_b = {k: v for k, v in SET_B_DEFAULTS.items()
             if k not in ctx.base_params}
    params = _merge_params(ctx, {**set_c_params, **set_b})

    per_code = _run_backtests(ctx, ctx.dfs, params, segment="is")
    agg = _aggregate(per_code)
    ev = LossEvaluation.from_aggregate(agg)

    bundle = CALMAR_LOSS.evaluate(ev)
    bundle["risk_score"] = None
    if not bundle.get("no_trades") and not bundle.get("violation"):
        from strategy.factors_and_algos._optm_engine.risk_score import (
            official_risk_score,
        )
        bundle["risk_score"] = official_risk_score(per_code, ctx.dfs)
    bundle.update({
        "pnl": round(agg["total_pnl"], 4),
        "capital": round(ev.total_capital, 4),
        "max_dd_abs": round(ev.max_dd_abs, 6),
        "dd_trough_date": agg["dd"].get("trough_date"),
        "n_decisions": agg["n_decisions"],
        "per_code": _per_code_summary(per_code),
    })
    return bundle


def make_objective_set_a(ctx: OptmContext):
    """Build the Optuna objective closure for Stage A (TPE, Omega loss)."""

    def objective(trial) -> float:
        from strategy.factors_and_algos._optm_engine.space import (
            suggest_set_a_params,
        )

        trial_params = suggest_set_a_params(trial, ctx.backtester)
        metrics = evaluate_set_a(ctx, trial_params)
        trial.set_user_attr("metrics", metrics)
        trial.set_user_attr("params_used", trial_params)
        return metrics["loss"]

    return objective


def make_objective_set_b(ctx: OptmContext, set_a_params: dict):
    """Build the Optuna objective closure for Stage B (TPE, Calmar loss)."""

    def objective(trial) -> float:
        from strategy.factors_and_algos._optm_engine.space import (
            suggest_set_b_params,
        )

        trial_params = suggest_set_b_params(trial)
        metrics = evaluate_set_b(ctx, trial_params, set_a_params)
        trial.set_user_attr("metrics", metrics)
        trial.set_user_attr("params_used", trial_params)
        return metrics["loss"]

    return objective


__all__ = [
    "OptmContext",
    "evaluate_set_a",
    "evaluate_set_b",
    "evaluate_set_c",
    "make_objective_set_a",
    "make_objective_set_b",
]
