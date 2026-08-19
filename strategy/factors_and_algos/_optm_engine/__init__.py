"""strategy.factors_and_algos._optm_engine — two-stage regime-aware
param optimizer (Optuna TPE + vanilla grid).

The "train model" engine for the pluggable algos. It works EXCLUSIVELY
through the :class:`~strategy.factors_and_algos._algo.AlgoBase` contract
(fetch_signal_data / apply_signals / run_backtest / TUNABLE_SPACE), so any
inherited algo that declares a ``TUNABLE_SPACE`` is optimizable without
engine changes.

Regime-aware loss splitting (see ``loss/``)
-------------------------------------------
Two parameter regimes, each with its own optimizer and objective —
applying one loss to both ruins the optimization:

  Set A — SIGNAL params (path-independent): the algo's ``TUNABLE_SPACE``
    (e.g. MACD EMA spans / thresholds / weights) + ``conf_threshold``.
    Optimizer: Optuna (TPE). Loss: −Omega, where Omega is the Omega
    Ratio of the per-exit return rates — upside magnitude is ignored so
    TPE can't chase leverage; downside is heavily penalized (win
    probability is what signal tuning wants). Hard constraint: monthly
    PnL positive in > 55% of rolling months (busting penalty).

  Set B — EXECUTION params (path-dependent, multiplicative):
    ``buy_exec_delay`` / ``sell_exec_delay`` (exit lags) +
    ``min_holding_period`` (hold-period offset). Optimizer: vanilla grid
    (default) or a separate TPE study. Loss: −Calmar, where Calmar =
    total return (OOS) / max drawdown — capital preservation over pure
    returns. Hard constraint: max drawdown ≤ 25% of peak capital;
    breaching candidates are instantly discarded.

Search space (see ``space.py``)
-------------------------------
  Set A (signal regime): ``conf_threshold`` + the algo's TUNABLE_SPACE.
  Set B (execution regime): ``buy_exec_delay``, ``sell_exec_delay``,
  ``min_holding_period`` — trading-layer knobs read by
  ``AlgoBase.run_backtest`` / the execution engine (algo-agnostic).
  ``COMMON_SPACE`` is the union (kept for documentation/compat).

IS/OOS split (``--oos-frac``, default 0.2)
-------------------------------------------
  Stage A (Omega) evaluates on the IS split (first ~80% of each code's
  rows); Stage B (Calmar) evaluates on the OOS split (last ~20%) so the
  execution params are graded out-of-sample. ``--oos-frac 0`` runs both
  stages on the full series.

Statics (``--statics-json``)
----------------------------
  Execution-cost assumptions are NOT searched — they are fixed inputs:
  ``fee_rate`` (default FEE_RATE), ``slippage_band`` (default
  SLIPPAGE_BAND), ``buy_notional``. They thread through ``params`` into
  the engine's worst-case fill / fee formulas.

Result persistence (see ``persist.py``)
---------------------------------------
  The combined best params (Set A ∪ Set B) are UPSERTED into
  ``strategy.algo_configs`` (wide-range row 1900-01-01..9999-12-31) for
  each (sec_type, code, strategy_name=algo_name), so the next
  "Run Strategy" run automatically picks them up via ``load_params``.

GPU (shared util in ``_common.df_utils._cudf_pandas``)
------------------------------------------------------
  ``--gpu auto|on|off`` — when the shared detector (nvidia-smi + cuDF
  smoke test) reports a working CUDA GPU, cudf.pandas is enabled at
  process start so ALL pandas signal math is transparently
  GPU-accelerated; otherwise falls back to CPU pandas. (Process-level
  decision — cudf.pandas patches the pandas import, so it must be
  enabled before pandas is first imported; the op-level
  ``_common.df_utils.should_use_gpu`` router convention does not apply
  here.)
"""
from __future__ import annotations

# LAZY (PEP 562): space.py/objective.py/loss pull in pandas via the
# algo stack. ``python -m strategy.factors_and_algos._optm_engine`` runs
# this __init__ before __main__.py's GPU decision, so eager imports here
# would defeat the cudf.pandas import hook (see
# ``_common.df_utils._cudf_pandas``).

__all__ = [
    # space
    "SET_A_EXEC_SPACE",
    "SET_B_SPACE",
    "SET_B_DEFAULTS",
    "SET_B_GRID_STEPS",
    "COMMON_SPACE",
    "suggest_set_a_params",
    "suggest_set_b_params",
    "set_b_grid",
    "snap_categorical_params",
    # objective
    "OptmContext",
    "evaluate_set_a",
    "evaluate_set_b",
    "make_objective_set_a",
    "make_objective_set_b",
    # loss (Set A / Set B — class-based, see loss/)
    "RegimeLoss",
    "LossEvaluation",
    "OmegaLoss",
    "CalmarLoss",
    "OMEGA_LOSS",
    "CALMAR_LOSS",
    "trade_returns",
    "monthly_pnl",
    "monthly_pnl_from_decisions",
    "aggregate_monthly_pnl",
    "positive_month_fraction",
    "equity_max_drawdown",
    # nested training (the 5-step master plan, see training/)
    "NestedTrainer",
    "Candidate",
    "CandidateGridResult",
    "TrainingResult",
    "KellyResult",
    "analytical_kelly",
    "KELLY_CAP",
    "KELLY_FRACTION",
    "MIN_TRADES",
]

_SPACE_NAMES = {
    "SET_A_EXEC_SPACE", "SET_B_SPACE", "SET_B_DEFAULTS",
    "SET_B_GRID_STEPS", "COMMON_SPACE",
    "suggest_set_a_params", "suggest_set_b_params", "set_b_grid",
    "snap_categorical_params",
}
_OBJECTIVE_NAMES = {
    "OptmContext", "evaluate_set_a", "evaluate_set_b",
    "make_objective_set_a", "make_objective_set_b",
}
_LOSS_NAMES = {
    "RegimeLoss", "LossEvaluation", "OmegaLoss", "CalmarLoss",
    "OMEGA_LOSS", "CALMAR_LOSS",
    "trade_returns", "monthly_pnl", "monthly_pnl_from_decisions",
    "aggregate_monthly_pnl", "positive_month_fraction",
    "equity_max_drawdown",
}
_TRAINING_NAMES = {
    "NestedTrainer", "Candidate", "CandidateGridResult", "TrainingResult",
    "KellyResult", "analytical_kelly",
    "KELLY_CAP", "KELLY_FRACTION", "MIN_TRADES",
}


def __getattr__(name: str):
    if name in _SPACE_NAMES:
        from strategy.factors_and_algos._optm_engine import space
        return getattr(space, name)
    if name in _OBJECTIVE_NAMES:
        from strategy.factors_and_algos._optm_engine import objective
        return getattr(objective, name)
    if name in _LOSS_NAMES:
        from strategy.factors_and_algos._optm_engine import loss
        return getattr(loss, name)
    if name in _TRAINING_NAMES:
        from strategy.factors_and_algos._optm_engine import training
        return getattr(training, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
