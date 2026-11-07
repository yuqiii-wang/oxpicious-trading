"""KellyResult — data definition.

Migrated verbatim from :mod:`strategy.factors_and_algos._optm_engine.training.trainer` (one file per ``@dataclass``)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class KellyResult:
    """Analytical Kelly outcome for one candidate's raw signal returns."""

    n_trades: int
    #: μ — mean per-exit return.
    mean: float
    #: σ² — sample variance (ddof=1) of per-exit returns.
    variance: float
    #: f* = μ/σ², floored at 0 (negative edge → never bet).
    full_kelly: float
    #: min(f*, cap) — the 20% portfolio-risk cap applied.
    capped_kelly: float
    #: capped × fraction — the quarter-Kelly betting fraction.
    fractional_kelly: float
    #: base_notional × fractional_kelly — the static base amount for
    #: Set B (fixed position size during the execution grid).
    notional: float
