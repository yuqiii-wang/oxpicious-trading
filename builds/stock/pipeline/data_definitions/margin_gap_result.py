"""MarginGapResult — data definition.

Migrated verbatim from :mod:`builds.stock.pipeline.margin_gap` (one file per ``@dataclass``)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass
class MarginGapResult:
    """Dates needing margin/liquidity (re)loads, split by gap kind."""
    target_dates: set[date]
    missing_margin_dates: set[date] = field(default_factory=set)
    partial_margin_dates: set[date] = field(default_factory=set)
    missing_liq_dates: set[date] = field(default_factory=set)
    missing_liquidity_dates: set[date] = field(default_factory=set)
