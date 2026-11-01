"""The signal family registry (analysis.analysis_signals.engines.
signal_families) — the detection families, each a set of RuleEntry
subclasses (engines/rule_base.py).

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
  rebound       the drop→rebound→resolution family (2026-10-26): the
                ma-spread rebound study's graduates as rule cells —
                the big drop from the trailing-Wd high activates the
                episode, the trough-break (capitulation BUY), the
                stall just below the last high (failed-reclaim SELL,
                exit timing) and the full reclaim (continuation BUY)
                are the causal resolutions (the PE-conditioned variant
                half unlinked 2026-11-01 — the plain population only)
  macd          the classic MACD crossover family (2026-10-26): the
                typical-EMA study's product — the classic 12/26/9
                spans (the tech_stats ema12/ema26 replaced the nearby
                non-standard span 10 the same day) and the classic
                signal-line crossover as a rule cell: cross-up BUY /
                cross-down SELL (exit timing), the value source
                computing the construction in its fetch from the base
                closes
  hedge_pairs   the PORTFOLIO family (fresh 2026-10-24): the typical
                negative-correlation trio — two offset-opposite leg
                curves + the broad market, all three in their own RSI
                capitulation tails the same day (the co-wash BUY of
                the inverse-vol long-long pair) + the blow-off mirror
                as the equity-asymmetry check; one registry entry
                owns the pair-native cells, the mov_rsi SOURCE layer
                at the market + leg codes (the is_in_portfolio TRUE
                tier) and all wipe scopes

The options families run their OWN universe — the sec_type's option
underlyings from stats.options_terms
(fetch/inputs.resolve_options_universe; options_wall drops the SSE ETF
underlyings, whose chain OI is an accumulated-volume estimator). The
hedge_pairs universe is likewise data-driven — the disjoint
offset-opposite industry pairs from the offsets table
(fetch/inputs._hedge_pairs.resolve_hedge_pair_universe; --codes is
ignored for the family).
"""
from __future__ import annotations

from analysis.analysis_signals.engines.rule_base import RuleEntry
from analysis.analysis_signals.engines.signal_families.boll_std import (
    BollBandEntry,
)
from analysis.analysis_signals.engines.signal_families.hedge_pairs import (
    HedgePairTrioEntry,
)
from analysis.analysis_signals.engines.signal_families.macd import (
    MacdCrossEntry,
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
from analysis.analysis_signals.engines.signal_families.rebound import (
    ReboundEntry,
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
    "rebound": [ReboundEntry],
    "macd": [MacdCrossEntry],
    "hedge_pairs": [HedgePairTrioEntry],
}

# families whose universe is the option underlyings, not the study
# indices (resolved per sec_type by fetch/inputs.resolve_options_
# universe; options_wall drops the SSE ETF underlyings)
OPTIONS_FAMILIES = frozenset({"options_wall", "options_vix"})

# the PORTFOLIO families (label → the portfolio_type label their
# groups ride in analysis_signals.portfolio_signal_rules): the family
# writes the portfolio tier tables in the same run as its rules —
# portfolio_signal_rules (one row per portfolio, wiped + rewritten),
# history_portfolio_signal_performance (the dated benefit audit,
# upsert-only) and portfolio_history_signals (the event tier with the
# member association; force purges the (sec_type, portfolio_type)
# scope, incremental upserts past max date)
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
    "FAMILY_PORTFOLIO_TYPE",
    "family_label",
]
