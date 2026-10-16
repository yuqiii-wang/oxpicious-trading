"""Configuration for live.live_signals — one file per concern (config/
tables, config/runtime, config/breach) plus the semantics doc here.

The historical ``live.live_signals.config.X`` import path keeps
working: this package re-exports every public constant.

Live breach check of the analysis_signals RULE CELLS (the LIVE signal
tier — a breach of an active rule cell's bar IS the live signal; the
rules-era rewire 2026-10-15, after signal_thresholds was dropped with
the 2026-10-14 redesign):

  - The current threshold set = the analysis_signals.signal_rules rows
    of the LIVE-WIRED families (the SIGNAL_VALUE_SOURCE keys): one row
    per (code, sub, trend, regime, action) CELL, each carrying its own
    action, bar definition (rule_params.bar_def) and confidence scale
    (rule_params.confidence).
  - The check is GENERIC — no per-family logic anywhere in the live
    tier. Every active cell carries its own decision and evidence:
    action ('sell' | 'buy') → the breach direction (sell breaches
    above its bar, buy below), the bar resolved from the cell's OWN
    bar_def (bollinger's fixed ±kσ constant; mov_rsi's per-code
    adaptive percentile tail, computed from the trailing RSI history
    at check time), confidence base_pct + depth scale (the SCALED
    breach confidence is computed over the normalized breach depth and
    recorded — the identical formula the history writer applies). The
    DAY-CONTEXT MATCH is the rules-era gate: only the cell whose
    (trend, regime) equals the day's own context (the signal rules
    tier's analysis.trend_days classification + stats.market_regimes) may
    fire. The only family knowledge left is the declarative
    SIGNAL_VALUE_SOURCE map (live_signals/analysis/fetch) saying WHERE
    the family's current value comes from (the current RSI row / the
    on-the-fly σ-depth z).
  - Triggered cells are recorded in live.live_signals (PK upsert —
    re-running the same (bar date, time) observation updates in
    place). Missing current value / underivable bar / no trend context
    ⇒ not comparable ⇒ skipped, never invented.

--signal-scheme selects the threshold source: 'analysis' (the only
scheme implemented) reads analysis_signals.signal_rules;
'strategy' is reserved for a future strategy.*-sourced threshold set.
"""
from __future__ import annotations

from .tables import *  # noqa: F401,F403
from .runtime import *  # noqa: F401,F403
from .breach import *  # noqa: F401,F403
