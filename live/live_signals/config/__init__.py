"""Configuration for live.live_signals — one file per concern (config/
tables, config/runtime, config/breach) plus the semantics doc here.

The historical ``live.live_signals.config.X`` import path keeps
working: this package re-exports every public constant.

Live breach check of the analysis_signals threshold set (the LIVE
signal tier — a breach of an active threshold IS the live signal):

  - The current threshold set = the ``is_active`` rows of
    analysis_signals.signal_thresholds (each config's LATEST
    end_date — the freshest snapshot owning it).
  - The check is GENERIC — no per-family logic anywhere in the live
    tier. Every active row carries its own decision and evidence:
    action ('sell' | 'buy') → the breach direction (sell breaches
    above its threshold, buy below), signal_threshold (stored in the
    underlying value's own space — the mov_rsi percentile bar is
    SIGNED BY SIDE at the analysis layer), confidence_base + the
    scale model (the SCALED breach confidence is computed over the
    breach depth and recorded). The only family knowledge left is the declarative
    SIGNAL_VALUE_SOURCE map (live_signals/analysis/fetch) saying WHERE
    the family's current value comes from (the current RSI row).
    mov_rsi's bar is static in its own space — the stored threshold
    (the derived-bar families mov_std / the crosses and their
    once-per-episode machinery were RETIRED with their strategies
    2026-10-05).
  - Triggered configs are recorded in live.live_signals (PK upsert —
    re-running the same (bar date, time) observation updates in
    place). Missing current value ⇒ not comparable ⇒ skipped, never
    invented.

--signal-scheme selects the threshold source: 'analysis' (the only
scheme implemented) reads analysis_signals.signal_thresholds;
'strategy' is reserved for a future strategy.*-sourced threshold set.
"""
from __future__ import annotations

from .tables import *  # noqa: F401,F403
from .runtime import *  # noqa: F401,F403
from .breach import *  # noqa: F401,F403
