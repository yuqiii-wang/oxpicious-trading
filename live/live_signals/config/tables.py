"""DB tables + the live record layout of live.live_signals (config).

The signal tables the live tier reads and writes, the RSI indicator
source (the bollinger family's values are computed on the fly from the
price history — no source table), the trend-days store the day-context
cell matching reads, and the live.live_signals record's write columns /
PK (upsert arbiter).
"""
from __future__ import annotations

# ---- Tables -----------------------------------------------------------------

# The RULES-era threshold set: every signal_rules row IS current (the
# pipeline wipes and rewrites the family scope each run) — one row per
# (code, sec_type, signal_type, signal_sub_type, trend, regime_state,
# action) CELL, carrying its own action, bar_def and confidence scale
# in rule_params. (Retired 2026-10-15: analysis_signals.
# signal_thresholds, dropped with the rules redesign 2026-10-14.)
SIGNALS_TABLE = "analysis_signals.signal_rules"
LIVE_SIGNALS_TABLE = "live.live_signals"

# RSI indicator source (daily rows, one per sec_type/code/date).
RSI_TABLE = "analysis.mov_ave_rsi"

# The signal rules tier's per-(code, date) TREND classification
# (analysis.trends.trend_state.attach_trend, written by the
# analysis_signals pipeline) — the live check reads the latest row
# at-or-before the bar date to pick WHICH (trend, regime) rule cell
# may fire (the day-context match). MOVED with the store 2026-10-15
# (was analysis_signals.code_trend_days, dropped by the trends
# consolidation — the stale name left the live day-context gate
# reading a dead table).
TREND_DAYS_TABLE = "analysis.trend_days"

# ---- Record layout ----------------------------------------------------------

# live.live_signals columns written per breach record (PK first).
# is_day_close_trigger: FALSE on intraday-bar observations (live monitor
# AND the on-demand --date mode's intraday replay), TRUE on the --date
# mode's daily-close fallback rows (time 15:00).
# regime_state: the breach bar's DATE market regime — the
# stats.market_regimes day label (calm/hot/panic/quiet) — recorded
# regime context, never a gate.
# (The confidence_boost / confidence_selection cohort add-on
# columns were RETIRED with the forecast layer 2026-10-05 — the
# plain rsi-pct population is the only cohort; the DB columns stay
# for the schema, unwritten.)
LIVE_SIGNAL_COLUMNS = [
    "code", "sec_type", "signal_type", "signal_sub_type", "date", "time",
    "action", "signal_excess", "signal_excess_pct", "signal",
    "signal_threshold", "confidence",
    "is_day_close_trigger",
    "regime_state",
]

# PK of live.live_signals (upsert arbiter).
LIVE_SIGNAL_PK = [
    "code", "sec_type", "signal_type", "signal_sub_type", "date", "time",
]

__all__ = [
    "SIGNALS_TABLE",
    "LIVE_SIGNALS_TABLE",
    "RSI_TABLE",
    "TREND_DAYS_TABLE",
    "LIVE_SIGNAL_COLUMNS",
    "LIVE_SIGNAL_PK",
]
