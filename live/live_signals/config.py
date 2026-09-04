"""Configuration for live.live_signals.

Live breach check of the analysis_signals threshold set for ONE code:

  - The current threshold set = the ``is_active`` rows of
    analysis_signals.signals (the sec_type's latest signal date).
  - mov_std configs compare the LIVE INTRADAY CLOSE (latest
    stats.{sec_type}_intraday_5min bar) against the band level — price
    space; the compared value is recorded in ``signal``.
  - mov_rsi configs compare the CURRENT RSI (latest
    analysis.mov_ave_rsi row, per window) against the top/bottom-1%
    threshold — indicator space (an RSI value is not a price; the
    breach record stores the RSI in ``signal``).
  - Triggered configs are recorded in live.live_signals (PK upsert —
    re-running the same (bar date, time) observation updates in place).

--signal-scheme selects the threshold source: 'analysis' (the only
scheme implemented) reads analysis_signals.signals; 'strategy' is
reserved for a future strategy.*-sourced threshold set.
"""
from __future__ import annotations

# ---- Tables -----------------------------------------------------------------

SIGNALS_TABLE = "analysis_signals.signals"
LIVE_SIGNALS_TABLE = "live.live_signals"

# RSI indicator source (daily rows, one per sec_type/code/date).
RSI_TABLE = "analysis.mov_ave_rsi"

# ---- Identity ---------------------------------------------------------------

PIPELINE_NAME = "live_signals"
PIPELINE_DESCRIPTION = (
    "Live breach check of the analysis_signals threshold set for one "
    "code: fetch the code's latest intraday close (stats.*_intraday_5min; "
    "404 when none), evaluate every active analysis_signals.signals "
    "config — mov_std close vs band level (price space), mov_rsi current "
    "RSI (analysis.mov_ave_rsi latest row) vs top/bottom-1% threshold "
    "(indicator space) — and record triggered breaches in "
    "live.live_signals (confidence fixed 100 for now)."
)

# ---- Schemes ----------------------------------------------------------------

SIGNAL_SCHEMES = ("analysis", "strategy")

# ---- Intraday price source per sec_type (probe order) ------------------------

# Probe order: most common live-check targets first. A code normally
# exists in exactly ONE table — the sec_type is derived from the hit.
INTRADAY_TABLES = {
    "index": "stats.index_intraday_5min",
    "etf": "stats.etf_intraday_5min",
    "stock": "stats.stock_intraday_5min",
}
SEC_TYPE_PROBE_ORDER = ("index", "etf", "stock")

# ---- Breach semantics --------------------------------------------------------

# side (from params JSON) → comparison direction (the sign of the
# record's signal_excess = signal - signal_threshold).
#   top/upper    → upward breach: triggered when value > threshold
#                  (signal_excess > 0, action sell)
#   bottom/lower → downward breach: triggered when value < threshold
#                  (signal_excess < 0, action buy)
BREACH_ABOVE_SIDES = ("top", "upper")

# ---- Record defaults ---------------------------------------------------------

# (confidence is NOT a default anymore: writers store
# ROUND(100 × analysis_signals.signals.confidence) — the source
# forecast confidence (reverse_prob probability, [0,1]) on the live
# 0-100 INTEGER scale. The scaling happens SQL-side in the writers'
# fetches; live.live_signals.confidence keeps DEFAULT 100 for legacy.)

# Decimal scale of live.live_signals.signal (NUMERIC(16,4)): records
# round the compared value to this scale BEFORE writing signal, and
# signal_excess = rounded_signal - signal_threshold, so the stored
# identity signal_excess = signal - signal_threshold holds exactly.
SIGNAL_SCALE = 4

# Decimal scale of live.live_signals.signal_excess_pct (NUMERIC(12,4)):
# signal_excess_pct = signal_excess / |signal_threshold| * 100 — rounded
# to this scale so the stored identity holds exactly. Guarded against
# divide-by-zero (None when signal_threshold == 0).
SIGNAL_PCT_SCALE = 4

# live.live_signals columns written per breach record (PK first).
LIVE_SIGNAL_COLUMNS = [
    "code", "sec_type", "signal_type", "signal_sub_type", "date", "time",
    "action", "signal_excess", "signal_excess_pct", "signal",
    "signal_threshold", "confidence",
]
# PK of live.live_signals (upsert arbiter).
LIVE_SIGNAL_PK = [
    "code", "sec_type", "signal_type", "signal_sub_type", "date", "time",
]
