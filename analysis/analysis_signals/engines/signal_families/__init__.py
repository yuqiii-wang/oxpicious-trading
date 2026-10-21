"""The signal family registry (analysis.analysis_signals.engines.
signal_families) — the eight detection families, each a set of
RuleEntry subclasses (engines/rule_base.py).

  mov_rsi       RSI percentile tails (per-code adaptive quantile bars)
  bollinger     fixed-±kσ band breach (z computed in-engine)
  ma_cross      price-vs-MA events: the conditioned reclaim, the
                failed-rally retest, the raw-gap breach (buy-only) +
                the market-level breadth rules (Zweig thrust / 9:1
                days, 2026-10-16)
  options_wall  VRP-gated OI-wall breach (+ the wait _rej twin)
  options_vix   the corr×spike interaction in the deep-fear regime +
                the vol-ATH panic-peak escalation leg + the ATR
                volatility-expansion breakout (2026-10-16)
  momentum      log-return tail breaches over the vol-adaptive
                momentum lookbacks (per-code adaptive quantile bars)
  rf_break      the roof/floor Donchian breakout (the prior day's
                window-extreme level — a STRUCTURAL bar; 2026-10-16)
  hedge_pairs   the long–long anti-correlated industry-pair family
                (2026-10-18, market-decoupled then merged into the
                TWO-LAYER design same day): the SOURCE layer is the
                components' existing Short-Term-Tech rsi signal rules
                (REAL mov_rsi rows at the market + leg codes); the
                ORCHESTRATION layer (HedgeRsiEntry, signal_type
                hp_rsi) coordinates them into the portfolio composites
                at the pair codes — the three-component capitulation
                entry paired within ~5 traded days, the market-high
                exit, the laggard's catch-up — each group referencing
                the sourced rule ids it orchestrates; the PAIR-NATIVE
                layer (HedgePairEntry, signal_type hedge_pairs) keeps
                the harvest ratio trims, the hedge-regime break stop
                and the observational flip — universe = the offsets
                table's top offset-opposite pairs (SELF_UNIVERSE)

The options families run their OWN universe — the sec_type's option
underlyings from stats.options_terms
(fetch/inputs.resolve_options_universe; options_wall drops the SSE ETF
underlyings, whose chain OI is an accumulated-volume estimator).
"""
from __future__ import annotations

from analysis.analysis_signals.engines.rule_base import RuleEntry
from analysis.analysis_signals.engines.signal_families.boll_std import (
    BollBandEntry,
)
from analysis.analysis_signals.engines.signal_families.hedge_pairs import (
    HedgePairEntry,
    HedgeRsiEntry,
)
from analysis.analysis_signals.engines.signal_families.ma_cross import (
    MaBreadthEntry,
    MaCrossReclaimEntry,
    MaRetestEntry,
    MaGdepthEntry,
)
from analysis.analysis_signals.engines.signal_families.momentum import (
    MomentumTailEntry,
)
from analysis.analysis_signals.engines.signal_families.mov_rsi import (
    RsiTailEntry,
)
from analysis.analysis_signals.engines.signal_families.options_vix import (
    OvixAtrBreakEntry,
    OvixFearUnwindEntry,
    OvixPanicPeakEntry,
    OvixSpikeEntry,
)
from analysis.analysis_signals.engines.signal_families.options_wall import (
    WallBreachEntry,
)
from analysis.analysis_signals.engines.signal_families.rf_break import (
    RoofFloorEntry,
)

# registry order = pipeline order (the signal_type label's canonical
# ordering); each family's entries run over ONE shared fetch
SIGNAL_FAMILY_ENTRIES: dict[str, list[type[RuleEntry]]] = {
    "mov_rsi": [RsiTailEntry],
    "bollinger": [BollBandEntry],
    "ma_cross": [MaCrossReclaimEntry, MaRetestEntry, MaGdepthEntry,
                 MaBreadthEntry],
    "options_wall": [WallBreachEntry],
    "options_vix": [OvixSpikeEntry, OvixPanicPeakEntry,
                    OvixAtrBreakEntry, OvixFearUnwindEntry],
    "momentum": [MomentumTailEntry],
    "rf_break": [RoofFloorEntry],
    # the hedge pair runs TWO entries over ONE shared prepared frame:
    # the RSI-sourced orchestration first (it also writes the sourced
    # type-0 component rows), then the pair-native rules
    "hedge_pairs": [HedgeRsiEntry, HedgePairEntry],
}

# families whose universe is the option underlyings, not the study
# indices (resolved per sec_type by fetch/inputs.resolve_options_
# universe — options_wall drops the SSE ETF underlyings)
OPTIONS_FAMILIES = frozenset({"options_wall", "options_vix"})

# families whose universe is a DERIVED pair/industry scope, not the
# --codes list (resolved at run time from their own analysis tables —
# --codes is ignored for them)
SELF_UNIVERSE_FAMILIES = frozenset({"hedge_pairs"})

# families whose codes are PORTFOLIOS → the portfolio_type label their
# groups register under (analysis_signals.portfolio_signal_rules, the
# per-sub-type groups with the signal_rule_ids BIGINT[] arrays; the
# codes are composite group ids, not securities). The portfolio_type
# is the PORTFOLIO tier's own vocabulary (static.label_dim
# 'portfolio_type') — deliberately not the signal family.
FAMILY_PORTFOLIO_TYPE: dict[str, str] = {
    "hedge_pairs": "neg_corr_hedge_pair",
}


def family_label(label: str) -> str:
    """Accept 'boll_std'/'bollinger' style aliases for CLI input."""
    return "bollinger" if label in ("boll_std", "bollinger") else label


__all__ = [
    "RuleEntry",
    "SIGNAL_FAMILY_ENTRIES",
    "OPTIONS_FAMILIES",
    "SELF_UNIVERSE_FAMILIES",
    "FAMILY_PORTFOLIO_TYPE",
    "family_label",
]
