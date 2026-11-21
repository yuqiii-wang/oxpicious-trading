"""Per-trading-date family weights (the edge model's output carrier).

Data carrier + lookup only; the statistics that fill it live in
:mod:`strategy.factors_and_algos._comb.edge`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Dict


@dataclass
class WeightTable:
    """``{trading_date: {family: normalized_weight}}`` over the union
    calendar of a run.

    Weights are already normalized (sum to 1 over the families the model
    knows, starved families filled with the mean-known weight, all-unknown
    dates fall back to equal). The blend re-normalizes over the actually
    selected families, so the absolute scale is not load-bearing — only
    the RATIOS between families on a date are.
    """

    weights: Dict[date, Dict[str, float]] = field(default_factory=dict)

    def at(self, day: date) -> Dict[str, float]:
        """Weights known on ``day`` (empty dict when the date is unknown)."""
        return self.weights.get(day, {})

    def families(self):
        """The family vocabulary covered by the table."""
        for per_fam in self.weights.values():
            return list(per_fam)
        return []
