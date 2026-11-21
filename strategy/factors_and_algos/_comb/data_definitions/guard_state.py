"""Equity-guard pass-2 state (per-date BUY multiplier).

Data carrier + lookup only; the pass-1 PnL-path logic that fills it lives
in :mod:`strategy.factors_and_algos._comb.guard`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Dict


@dataclass
class GuardState:
    """``{trading_date: buy_multiplier}`` derived from the pass-1 pooled
    PnL path (1.0 = no guard, 0.5 = negative trailing slope). Dates not
    in the map trade unguarded (warmup).
    """

    multipliers: Dict[date, float] = field(default_factory=dict)

    def at(self, day: date) -> float:
        """The buy multiplier effective on ``day`` (1.0 default)."""
        return self.multipliers.get(day, 1.0)
