"""LossEvaluation — data definition.

Migrated verbatim from :mod:`strategy.factors_and_algos._optm_engine.loss.base` (one file per ``@dataclass``)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List


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
