"""Regime-aware loss classes for the two-stage optimizer.

Two parameter regimes, two objectives — applying one loss to both
ruins the optimization because the regimes have different
sensitivities. Class-based design: ``RegimeLoss`` (``base.py``) holds
the shared badness-ordering template ONCE, and the two concrete losses
plug in their ratio + hard constraint + context fields:

  Set A — signal generation (MACD EMAs / thresholds): static,
      path-independent (given a price series the signal array is
      deterministic) → **Omega Ratio** of the per-exit return rates,
      hard-constrained to > 55% positive monthly PnL. Optimizer:
      Optuna TPE. (``OmegaLoss``)

  Set B — execution path (exit lags / holding-period offsets):
      path-dependent, multiplicative compounding → **Calmar Ratio**
      (total return / max drawdown) on the out-of-sample segment,
      hard-constrained to max drawdown ≤ 25% of equity. Optimizer:
      vanilla grid (or a separate TPE study). (``CalmarLoss``)

``OMEGA_LOSS`` / ``CALMAR_LOSS`` are the stateless module-level
singletons the optimizer uses. ``metrics.py`` holds the shared pure
primitives (per-trade returns, monthly equity Δ, cross-code max
drawdown) the objective layer aggregates into a ``LossEvaluation``.
"""
from __future__ import annotations

from strategy.factors_and_algos._optm_engine.loss.base import (
    LossEvaluation,
    RegimeLoss,
)
from strategy.factors_and_algos._optm_engine.loss.calmar import CalmarLoss
from strategy.factors_and_algos._optm_engine.loss.metrics import (
    aggregate_monthly_pnl,
    equity_max_drawdown,
    monthly_pnl,
    monthly_pnl_from_decisions,
    positive_month_fraction,
    trade_returns,
)
from strategy.factors_and_algos._optm_engine.loss.omega import OmegaLoss

#: Stateless singletons — the losses carry no per-call state.
OMEGA_LOSS = OmegaLoss()
CALMAR_LOSS = CalmarLoss()

__all__ = [
    # classes + singletons
    "RegimeLoss",
    "LossEvaluation",
    "OmegaLoss",
    "CalmarLoss",
    "OMEGA_LOSS",
    "CALMAR_LOSS",
    # shared metrics (used by the objective layer to build evaluations)
    "trade_returns",
    "monthly_pnl",
    "monthly_pnl_from_decisions",
    "aggregate_monthly_pnl",
    "positive_month_fraction",
    "equity_max_drawdown",
]
