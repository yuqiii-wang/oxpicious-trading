"""DB tables + the live record layout of live.live_signals (config).

The two signal tables the live tier reads and writes, the RSI
indicator source, and the live.live_signals record's write columns /
PK (upsert arbiter).
"""
from __future__ import annotations

# ---- Tables -----------------------------------------------------------------

SIGNALS_TABLE = "analysis_signals.signal_strategies"
LIVE_SIGNALS_TABLE = "live.live_signals"

# RSI indicator source (daily rows, one per sec_type/code/date).
RSI_TABLE = "analysis.mov_ave_rsi"

# ---- Record layout ----------------------------------------------------------

# live.live_signals columns written per breach record (PK first).
# is_day_close_trigger: FALSE on intraday-bar observations (live monitor
# AND the on-demand --date mode's intraday replay), TRUE on the --date
# mode's daily-close fallback rows (time 15:00).
# is_market_hyped: the breach bar's DATE sits inside one of the code's
# stats.mov_ave_market_hypes episodes (any check-in window) — recorded
# regime context, never a gate.
LIVE_SIGNAL_COLUMNS = [
    "code", "sec_type", "signal_type", "signal_sub_type", "date", "time",
    "action", "signal_excess", "signal_excess_pct", "signal",
    "signal_threshold", "confidence", "is_day_close_trigger",
    "is_market_hyped",
]

# PK of live.live_signals (upsert arbiter).
LIVE_SIGNAL_PK = [
    "code", "sec_type", "signal_type", "signal_sub_type", "date", "time",
]

__all__ = [
    "SIGNALS_TABLE",
    "LIVE_SIGNALS_TABLE",
    "RSI_TABLE",
    "LIVE_SIGNAL_COLUMNS",
    "LIVE_SIGNAL_PK",
]
