"""The σ ladder — the six next-day scenario projections
(strategy.forecast_next_day.scenarios).

σ20 = the population std (ddof=0, the bollinger σ convention) of the
trailing 20 close-to-close pct returns at the anchor date. From the
anchor's official close the ladder projects the next-day close at
base × (1 ± k·σ) for k ∈ {1, 2, 3} — six scenarios, the "20d 3,2,1
times std fluctuation ups and downs from today" the feature registers.

Pure host math over one code's scalar closes (a few thousand floats —
the sanctioned per-group boundary; this module never builds frames).
"""
from __future__ import annotations

import statistics
from datetime import date

from pydantic import BaseModel

from strategy.forecast_next_day import (
    DIRECTIONS,
    RETURNS_WINDOW,
    SIGMA_MULTS,
)


class ScenarioSpec(BaseModel):
    """One scenario's projection — the σ ladder member.

    The scenario constants denormalized onto every
    strategy.forecast_next_day row of the scenario derive from these
    fields (``forecast_close`` keeps full float precision; the writer
    rounds to the table's NUMERIC scales)."""

    anchor_date: date
    code: str
    direction: str          # 'down' | 'up'
    sigma_mult: int         # 1 | 2 | 3
    base_close: float
    sigma_20d: float        # return units (0.0123 = 1.23%)
    forecast_close: float
    forecast_change_pct: float

    @property
    def move(self) -> float:
        """The signed σ move in return units (−kσ / +kσ)."""
        return -self.sigma_mult * self.sigma_20d \
            if self.direction == "down" \
            else self.sigma_mult * self.sigma_20d


class SigmaLadder:
    """The per-code σ ladder builder — σ20 + the six ScenarioSpecs."""

    def sigma_20d(self, closes: list[float]) -> float | None:
        """The trailing-20-return population σ, in RETURN units.

        None when the code carries fewer than RETURNS_WINDOW+1 closes
        (the ladder is underivable — the code is skipped, never
        invented)."""
        if len(closes) < RETURNS_WINDOW + 1:
            return None
        rets = [
            closes[i] / closes[i - 1] - 1.0
            for i in range(len(closes) - RETURNS_WINDOW, len(closes))
        ]
        return statistics.pstdev(rets)

    def scenarios(
        self, code: str, anchor: date, closes: list[float],
    ) -> list[ScenarioSpec]:
        """The six ±kσ projections from the anchor's close (the last
        close of the list) — down(1,2,3) then up(1,2,3)."""
        sigma = self.sigma_20d(closes)
        if sigma is None or sigma <= 0.0:
            return []
        base = closes[-1]
        out: list[ScenarioSpec] = []
        for direction in DIRECTIONS:
            for mult in SIGMA_MULTS:
                move = mult * sigma \
                    * (-1.0 if direction == "down" else 1.0)
                close = base * (1.0 + move)
                out.append(ScenarioSpec(
                    anchor_date=anchor,
                    code=code,
                    direction=direction,
                    sigma_mult=mult,
                    base_close=base,
                    sigma_20d=sigma,
                    forecast_close=close,
                    forecast_change_pct=move * 100.0,
                ))
        return out
