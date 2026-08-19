"""Set B loss class — Calmar Ratio on the out-of-sample segment.

See ``CalmarLoss`` for the rationale; ``base.RegimeLoss`` for the shared
badness-ordering template.
"""
from __future__ import annotations

from typing import Any, ClassVar, Dict

from strategy.factors_and_algos._optm_engine.loss.base import (
    LossEvaluation,
    RegimeLoss,
)


class CalmarLoss(RegimeLoss):
    """Set B (execution regime) loss — minimize −Calmar.

    Set B (exit lags / hold-period offsets — the execution-path params)
    is path-dependent and multiplicative: it dictates how deep the
    equity curve plunges (doubling position size doubles volatility but
    changes max drawdown exponentially through path compounding), so
    the objective forces capital preservation over pure returns:

        Calmar = Total Return (OOS) / Maximum Drawdown

    Hard constraint: absolute drawdown must never exceed 25% of equity
    (peak capital deployed). Grid candidates that breach the cap are
    instantly discarded (``_pick_best_b`` in ``__main__``); a TPE stage
    B sees the graded rejection instead.
    """

    metric_name: ClassVar[str] = "calmar"

    #: Hard constraint: max drawdown (as a fraction of peak capital)
    #: must NEVER exceed 25%.
    MAX_DD_LIMIT: ClassVar[float] = 0.25
    #: Cap for the degenerate zero-drawdown Calmar — keeps the loss
    #: finite.
    CALMAR_CAP: ClassVar[float] = 1e6

    def metric(self, ev: LossEvaluation) -> float:
        return self.calmar_ratio(ev.total_return, ev.max_dd_pct)

    def constraint_ok(self, ev: LossEvaluation) -> bool:
        return ev.max_dd_pct <= self.MAX_DD_LIMIT

    def constraint_deficit(self, ev: LossEvaluation) -> float:
        """How far the drawdown breaches the 25% cap, normalized to [0, 1].

        A 30% DD (deficit 0.2) ranks better than a 45% DD (deficit 0.8)
        — the gradient the Set B search follows back under the cap.
        """
        deficit = (ev.max_dd_pct - self.MAX_DD_LIMIT) / self.MAX_DD_LIMIT
        return min(max(deficit, 0.0), 1.0)

    def extra_fields(self, ev: LossEvaluation) -> Dict[str, Any]:
        return {
            "total_return": round(ev.total_return, 6),
            "max_dd_pct": round(ev.max_dd_pct, 6),
            # Data-based breach flag — False for a no-trade evaluation
            # (nothing deployed ⇒ nothing breached).
            "violation": ev.max_dd_pct > self.MAX_DD_LIMIT,
        }

    def calmar_ratio(self, total_return: float, max_dd: float) -> float:
        """Calmar = total return / max drawdown, clamped to ±CALMAR_CAP.

        ``max_dd`` is the drawdown as a fraction of equity (peak
        capital deployed). A zero-drawdown positive return is the
        degenerate best case → +cap; a zero-drawdown negative return →
        −cap; exactly zero return → 0.
        """
        if max_dd <= 0.0:
            if total_return > 0.0:
                return self.CALMAR_CAP
            if total_return < 0.0:
                return -self.CALMAR_CAP
            return 0.0
        c = total_return / max_dd
        return max(min(c, self.CALMAR_CAP), -self.CALMAR_CAP)


__all__ = ["CalmarLoss"]
