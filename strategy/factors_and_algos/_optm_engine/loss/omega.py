"""Set A loss class — Omega Ratio of the raw signal returns.

See ``OmegaLoss`` for the rationale; ``base.RegimeLoss`` for the shared
badness-ordering template.
"""
from __future__ import annotations

from typing import Any, ClassVar, Dict, List, Optional

from strategy.factors_and_algos._optm_engine.loss.base import (
    LossEvaluation,
    RegimeLoss,
)
from strategy.factors_and_algos._optm_engine.loss.metrics import (
    positive_month_fraction,
)


class OmegaLoss(RegimeLoss):
    """Set A (signal regime) loss — minimize −Omega.

    Set A (MACD EMAs / thresholds / conf_threshold — everything that
    shapes the SIGNAL array) is path-independent: given a price series
    the signal array is deterministic. What matters from signal
    generation is the PROBABILITY of winning trades, not how big each
    win is — Omega ignores upside magnitude (so TPE can't chase
    bull-market leverage) and heavily penalizes any downside:

        Omega = Σ max(R_t − 0, 0) / Σ max(0 − R_t, 0)

    Hard constraint (regime consistency): monthly PnL must be positive
    in more than 55% of the rolling calendar months.
    """

    metric_name: ClassVar[str] = "omega"

    #: Return point separating gains from losses (per-trade break-even).
    OMEGA_THRESHOLD: ClassVar[float] = 0.0
    #: Hard constraint: positive monthly-PnL months must EXCEED 55%.
    MONTHLY_POSITIVE_MIN: ClassVar[float] = 0.55
    #: Cap for the degenerate all-wins Omega (Σ losses = 0) — keeps the
    #: loss finite for TPE.
    OMEGA_CAP: ClassVar[float] = 1e6

    def metric(self, ev: LossEvaluation) -> float:
        return self.omega_ratio(ev.returns)

    def constraint_ok(self, ev: LossEvaluation) -> bool:
        return positive_month_fraction(ev.monthly) > self.MONTHLY_POSITIVE_MIN

    def constraint_deficit(self, ev: LossEvaluation) -> float:
        """How far the positive-month share sits below the 55% bar.

        Deficit is normalized by the bar itself and clamped to [0, 1]:
        a 45%-positive trial (deficit ≈ 0.18) ranks clearly better than
        a 35%-positive one (deficit ≈ 0.36) — the gradient TPE follows
        toward >55% feasibility.
        """
        frac = positive_month_fraction(ev.monthly)
        deficit = (self.MONTHLY_POSITIVE_MIN - frac) / self.MONTHLY_POSITIVE_MIN
        return min(max(deficit, 0.0), 1.0)

    def extra_fields(self, ev: LossEvaluation) -> Dict[str, Any]:
        frac = positive_month_fraction(ev.monthly)
        return {
            "n_months": len(ev.monthly),
            "positive_month_fraction": round(frac, 4),
        }

    def omega_ratio(
        self, returns: List[float], threshold: Optional[float] = None,
    ) -> float:
        """Ω = Σ max(R−τ, 0) / Σ max(τ−R, 0), capped at OMEGA_CAP."""
        if threshold is None:
            threshold = self.OMEGA_THRESHOLD
        gains = sum(max(r - threshold, 0.0) for r in returns)
        losses = sum(max(threshold - r, 0.0) for r in returns)
        if losses <= 0.0:
            return self.OMEGA_CAP if gains > 0.0 else 0.0
        return min(gains / losses, self.OMEGA_CAP)


__all__ = ["OmegaLoss"]
