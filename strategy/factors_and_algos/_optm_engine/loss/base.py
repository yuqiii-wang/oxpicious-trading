"""Regime loss base class + the evaluation input it grades.

Both regime losses (``OmegaLoss`` for Set A, ``CalmarLoss`` for Set B)
share the same badness-ORDERING template — a constraint-violating trial
must always rank worse than any valid trial, and a never-trading param
set must rank worse than ANYTHING that trades:

    valid trial      → −metric                          (≤ 0)
    violating trial  → 100 + 20·deficit − discount      (∈ [90, 120])
    no trades        → NO_TRADE_LOSS                    (1000)

For violating trials the loss is graded by the normalized CONSTRAINT
DEFICIT first (how far the trial sits from feasibility, 0 = at the
boundary, 1 = far away) and the metric second — a flat
``100 − min(metric, cap)`` penalty gives the sampler ZERO gradient
toward the feasible region, so TPE stalls bouncing inside the penalty
band (loss "not decreasing"). With the deficit term, closer-to-feasible
trials always rank better and the metric breaks ties, so the sampler is
steered toward satisfying the hard constraint before maximizing the
ratio. The ``discount`` term is clamped to [0, GRADING_CAP] so a
pathological ratio (huge negative Calmar / the all-wins Omega cap) can
never push a violating trial above the no-trade penalty or below a
valid trial.

``RegimeLoss.evaluate`` is the template method implementing that
ordering once; subclasses supply the ratio (``metric``), the hard
constraint (``constraint_ok``), the normalized distance to feasibility
(``constraint_deficit``) and their metric-specific context keys
(``extra_fields``).
"""
from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import Any, ClassVar, Dict, List


@dataclass(frozen=True)
class LossEvaluation:
    """Aggregated cross-code backtest outputs a regime loss grades.

    Shaped from an ``objective._aggregate`` output dict via
    ``from_aggregate``; ``n_trades`` / ``total_return`` / ``max_dd_pct``
    are derived properties (single source of truth).
    """

    #: Pooled per-exit return rates R_t (one per SELL, all codes).
    returns: List[float]
    #: Aggregated monthly PnL Δ keyed 'YYYY-MM' (chronological).
    monthly: Dict[str, float]
    #: Aggregated max drawdown in the same normalized PnL units.
    max_dd_abs: float
    #: Σ realized PnL across codes (incl. the final-liquidation SELL).
    total_pnl: float
    #: Peak capital deployed across codes (compute_total_buy_cost).
    total_capital: float
    #: Total BUY + SELL decision count across codes.
    n_decisions: int

    @property
    def n_trades(self) -> int:
        return len(self.returns)

    @property
    def total_return(self) -> float:
        """Total PnL / peak capital (0.0 when nothing was deployed)."""
        if self.total_capital <= 0.0:
            return 0.0
        return self.total_pnl / self.total_capital

    @property
    def max_dd_pct(self) -> float:
        """Max drawdown as a fraction of peak capital (0.0 guard)."""
        if self.total_capital <= 0.0:
            return 0.0
        return self.max_dd_abs / self.total_capital

    @classmethod
    def from_aggregate(cls, agg: Dict[str, Any]) -> "LossEvaluation":
        """Shape an ``objective._aggregate`` output dict."""
        return cls(
            returns=list(agg.get("returns") or []),
            monthly=dict(agg.get("monthly") or {}),
            max_dd_abs=float((agg.get("dd") or {}).get("max_dd") or 0.0),
            total_pnl=float(agg.get("total_pnl") or 0.0),
            total_capital=float(agg.get("total_capital") or 0.0),
            n_decisions=int(agg.get("n_decisions") or 0),
        )


class RegimeLoss(abc.ABC):
    """Template for the two regime losses (Omega / Calmar).

    Stateless — carry no per-call state, so module-level singletons are
    safe (see ``loss/__init__.py``).
    """

    #: A param set that never trades is useless, not optimal — worse
    #: than ANY penalized-but-trading trial.
    NO_TRADE_LOSS: ClassVar[float] = 1000.0
    #: Trial-busting penalty when the hard constraint is violated.
    CONSTRAINT_PENALTY: ClassVar[float] = 100.0
    #: Weight of the normalized constraint deficit added on top of the
    #: penalty for violating trials — the PRIMARY gradient toward the
    #: feasible region (each 5pp of deficit ≈ +1 loss point).
    CONSTRAINT_DEFICIT_WEIGHT: ClassVar[float] = 20.0
    #: Grading cap for violating trials — the metric only discounts the
    #: penalty up to this value, so a pathological ratio (e.g. the
    #: all-wins Omega cap) can never flip a violating trial above a
    #: valid one.
    GRADING_CAP: ClassVar[float] = 10.0

    #: Bundle key holding this loss's ratio ('omega' / 'calmar').
    metric_name: ClassVar[str] = "metric"

    @abc.abstractmethod
    def metric(self, ev: LossEvaluation) -> float:
        """The ratio this loss maximizes (higher = better)."""

    @abc.abstractmethod
    def constraint_ok(self, ev: LossEvaluation) -> bool:
        """Hard-constraint check (False ⇒ graded rejection)."""

    @abc.abstractmethod
    def constraint_deficit(self, ev: LossEvaluation) -> float:
        """Normalized distance to feasibility, clamped to [0, 1].

        0.0 = sitting exactly at the constraint boundary, 1.0 = far
        away. Only consulted for violating trials — it is the gradient
        that steers the sampler toward the feasible region.
        """

    @abc.abstractmethod
    def extra_fields(self, ev: LossEvaluation) -> Dict[str, Any]:
        """Metric-specific context keys merged into every bundle."""

    def evaluate(self, ev: LossEvaluation) -> Dict[str, Any]:
        """Score one evaluation into a loss bundle (template method).

        Bundle keys: ``loss`` (minimized), ``<metric_name>``,
        ``n_trades``, ``constraint_ok``, ``no_trades``,
        ``constraint_deficit`` — plus whatever ``extra_fields``
        contributes.
        """
        if ev.n_trades == 0:
            return {
                "loss": self.NO_TRADE_LOSS,
                self.metric_name: 0.0,
                "n_trades": 0,
                "constraint_ok": False,
                "no_trades": True,
                "constraint_deficit": 1.0,
                **self.extra_fields(ev),
            }
        value = self.metric(ev)
        ok = self.constraint_ok(ev)
        deficit = 0.0 if ok else self.constraint_deficit(ev)
        if ok:
            loss = -value
        else:
            # Graded rejection, deficit FIRST: closer-to-feasible trials
            # always rank better; the metric discount (clamped to
            # [0, GRADING_CAP]) only breaks ties, so the sampler keeps
            # directional feedback without ever ranking a violating
            # trial above a valid one.
            discount = min(max(value, 0.0), self.GRADING_CAP)
            loss = (
                self.CONSTRAINT_PENALTY
                + self.CONSTRAINT_DEFICIT_WEIGHT * deficit
                - discount
            )
        return {
            "loss": round(loss, 6),
            self.metric_name: round(value, 6),
            "n_trades": ev.n_trades,
            "constraint_ok": ok,
            "no_trades": False,
            "constraint_deficit": round(deficit, 4),
            **self.extra_fields(ev),
        }


__all__ = ["LossEvaluation", "RegimeLoss"]
