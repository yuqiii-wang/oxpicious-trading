"""Configuration for analyze.analysis_signals — one file per signal
family (config/mov_rsi, config/mov_std, config/mov_pairs) plus the
cross-family constants here.

Tables, THE gate constants (the plain forecast-results rule, identical
for every family), the refresh window and the write column/PK layouts.
Reusable universe constants are imported from analysis_forecasts'
config — single source, never duplicated.
"""
from __future__ import annotations

import datetime

from analyze.analysis_forecasts.config import SEC_TYPES

from .mov_pairs import (
    PAIRS_SIGNAL_SIDES,
    PAIRS_SIGNAL_WINDOW_MIN,
    MOV_PAIRS_EMA_WINDOWS,
    MOV_PAIRS_WINDOWS,
)
from .mov_rsi import RSI_PCT, RSI_WINDOWS
from .mov_std import MA_WINDOWS, STD_K_MIN, STD_MA_WINDOW_MIN, STD_MULTIPLES

# ---- Tables -------------------------------------------------------------------

TABLE_STRATEGIES = "analysis_signals.signal_strategies"
TABLE_HISTORY = "analysis_signals.history_signals"

# Post-run maintenance SQL (executed with $1 = sec_type after every run).
SQL_IS_ACTIVE = "02_is_active.sql"
SQL_SIGNAL_ORDER = "03_signal_order_rank.sql"

# ---- Sides & actions -----------------------------------------------------------
#
# The sides whose breach is an UPWARD crossing (the live tier's
# ABOVE_ACTION): top/upper extremes → 'sell' (the measured reversal is
# downward). Every other side → 'buy'. Single source for the engines'
# action mapping AND the engines._quality breach check.

SELL_SIDES = ("top", "upper")
ABOVE_ACTION = "sell"
BELOW_ACTION = "buy"

# ---- The gate (the plain forecast-results rule, every family) ------------------
#
# On the bucket's MIXED forecast_results row: the sign-aligned blended
# mean forward change (dir_ave — top/upper negated, bottom/lower as-is;
# the sign alignment happens in the engine) > 0.75% AND the blended
# reverse_prob > 1%. confidence = reverse_prob.

GATE_DIR_AVE_MIN = 0.0075
GATE_REVERSE_PROB_MIN = 0.01

# ---- Refresh window --------------------------------------------------------------

# The newest N present stat_months are deleted + re-emitted on every
# run (mirrors analysis_forecasts' REFRESH_MONTHS: the RUNNING month —
# present there as a partial snapshot keyed at its month-end, recomputed
# daily — plus the last 4 COMPLETED months, whose long-horizon mixed
# rows carry truncated 20d/60d forward legs right after month-end).
REFRESH_MONTHS = 5

# ---- Stage keys (--metrics) ------------------------------------------------------

STAGE_NAMES = ("rsi", "std", "pairs", "epairs")

# ---- Record layouts ---------------------------------------------------------------

# signal_strategies columns in write order (PK first).
STRATEGY_COLUMNS = [
    "code", "sec_type", "signal_type", "signal_sub_type", "side",
    "is_market_hyped",
    "start_date", "end_date",
    "action", "signal_threshold", "confidence", "reason", "params",
    "signal_order", "is_active",
]
STRATEGY_PK = [
    "code", "sec_type", "signal_type", "signal_sub_type", "side",
    "is_market_hyped",
    "start_date", "end_date",
]

# history_signals columns in write order (PK first; created_at left to
# its DEFAULT).
HISTORY_COLUMNS = [
    "code", "sec_type", "signal_type", "signal_sub_type", "date", "time",
    "action", "signal_excess", "signal_excess_pct", "signal",
    "signal_threshold", "confidence", "is_day_close_trigger",
    "is_market_hyped",
]
HISTORY_PK = [
    "code", "sec_type", "signal_type", "signal_sub_type", "date", "time",
]

# History rows are day-close records.
HISTORY_TIME = datetime.time(15, 0)

# Decimal scales of the history record's numerics (mirror live's
# convention: signal rounds BEFORE the excess identity is computed so
# signal_excess = signal - signal_threshold holds exactly).
SIGNAL_SCALE = 4
THRESHOLD_SCALE = 6
EXCESS_SCALE = 6
EXCESS_PCT_SCALE = 4

__all__ = [
    "TABLE_STRATEGIES",
    "TABLE_HISTORY",
    "SQL_IS_ACTIVE",
    "SQL_SIGNAL_ORDER",
    "SELL_SIDES",
    "ABOVE_ACTION",
    "BELOW_ACTION",
    "GATE_DIR_AVE_MIN",
    "GATE_REVERSE_PROB_MIN",
    "RSI_PCT",
    "STD_MA_WINDOW_MIN",
    "STD_K_MIN",
    "PAIRS_SIGNAL_WINDOW_MIN",
    "PAIRS_SIGNAL_SIDES",
    "REFRESH_MONTHS",
    "STAGE_NAMES",
    "STRATEGY_COLUMNS",
    "STRATEGY_PK",
    "HISTORY_COLUMNS",
    "HISTORY_PK",
    "HISTORY_TIME",
    "SIGNAL_SCALE",
    "THRESHOLD_SCALE",
    "EXCESS_SCALE",
    "EXCESS_PCT_SCALE",
    "SEC_TYPES",
    "RSI_WINDOWS",
    "MA_WINDOWS",
    "STD_MULTIPLES",
    "MOV_PAIRS_WINDOWS",
    "MOV_PAIRS_EMA_WINDOWS",
]
