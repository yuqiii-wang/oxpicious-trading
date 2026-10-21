"""hedge_pairs — the long–long anti-correlated pair family (the
2026-10-18 hedge-portfolio study; see engine.py). TWO entries under
the ONE registry label: HedgeRsiEntry (the RSI-sourced orchestration
— signal_type hp_rsi + the sourced type-0 component rows) and
HedgePairEntry (the pair-native hedge_pairs rules)."""
from __future__ import annotations

from analysis.analysis_signals.engines.signal_families.hedge_pairs.engine import (
    HedgePairEntry,
    HedgeRsiEntry,
)

__all__ = ["HedgePairEntry", "HedgeRsiEntry"]
