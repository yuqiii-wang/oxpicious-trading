"""volatility — the realized-vol tail family (analysis.
analysis_signals.engines.signal_families.volatility, 2026-11-18): one
entry, the leverage-effect tail grid (axis × tail pct, both actions
per sub type)."""
from analysis.analysis_signals.engines.signal_families.volatility.engine import (
    VolatilityTailEntry,
)

__all__ = ["VolatilityTailEntry"]
