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

from analyze.analysis_forecasts.config import SEC_TYPES, TRIGGER_DELAY_MAX

from .mov_pairs import (
    PAIRS_SIGNAL_SIDES,
    PAIRS_SIGNAL_WINDOW_MIN,
    MOV_PAIRS_EMA_WINDOWS,
    MOV_PAIRS_WINDOWS,
)
from .mov_rsi import RSI_PCTS_EMIT, RSI_WINDOWS
from .mov_std import MA_WINDOWS, STD_K_MIN, STD_MA_WINDOW_MIN, STD_MULTIPLES

# ---- Tables -------------------------------------------------------------------

TABLE_STRATEGIES = "analysis_signals.signal_strategies"
TABLE_HISTORY = "analysis_signals.history_signals"
TABLE_EMISSIONS = "analysis_signals.snapshot_emissions"

# Post-run maintenance SQL (executed with $1 = sec_type after every run).
SQL_IS_ACTIVE = "02_is_active.sql"
SQL_SIGNAL_ORDER = "03_signal_order_rank.sql"
# The partial-backfill registry's idempotent DDL (executed once per run,
# no parameters — the engines' resolution stamps and diffs against it).
SQL_EMISSIONS_DDL = "04_snapshot_emissions.sql"

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
# the sign alignment happens in the engine) > 0.75%. (The blended
# reverse_prob > 1% leg was REMOVED 2026-09-25 with
# forecast_results.reverse_prob — see
# analyze/analysis_forecasts/config/horizons.py.)
#
# confidence = dir_ave — the chosen rung's sign-aligned blended mean
# forward change (the expected favorable move; the FLOAT value ranks
# signal_order). history/live rows denormalize it as the INTEGER
# ROUND(10000 x dir_ave) — basis points of expected move.

GATE_DIR_AVE_MIN = 0.0075

# ---- The delay ladder (the optimal entry-delay selection) ----------------------
#
# Every rung 0..TRIGGER_DELAY_MAX of the bucket's mixed forecast_results
# ladder (each rung's stats conditioned on the signal having persisted
# that long — analyze.analysis_forecasts.config.buckets) faces THE gate;
# the bucket's OPTIMAL ENTRY DELAY is the gate-passing rung maximizing
#
#     score(d) = sign-aligned dir_ave(d) × occurrence_count(d)
#
# — the total blended forward move the entry rule captures over the
# window, balancing the OPPORTUNITY COST of waiting (occurrence_count
# decays with the rung: only streaks that persisted reach it) against
# the RETURN (the persistence-conditioned mean reversal deepens with
# the rung). Ties → the smallest delay; a bucket with no gate-passing
# rung never registers. The strategy's confidence/params profile and
# its history rows are the CHOSEN rung's (its day-d anchors).

SIGNAL_DELAY_MAX = TRIGGER_DELAY_MAX

# ---- The robustness floors (the 2026-09 quality-v2 calibration) ---------
#
# Applied at rung CANDIDACY in detect (before the balance-rule argmax),
# they kill the small-sample cells the gates alone would register:
#
#   - *_SAMPLE_FLOOR: the rung's occurrence_count minimum — the number
#     of 10-year-window streaks that persisted to day d. Below it the
#     gate's dir_ave is selection noise (winner's bias).
#   - *_STABILITY_MIN: the sign-aligned dir_ave must be positive on at
#     least this many of the ladder's early rungs — the edge must show
#     DIRECTION before it clears the gate's magnitude. Streak families
#     only (a one-day family has no persistence axis).
#
# Per-SIDE calibration (2026-09 v2.1 fine-tune, measured in
# temp_scripts/study_sell_floors.py): a SELL's evidence is structurally
# thinner in positive-drift markets — overbought extremes continue
# often enough that the persistence-conditioned direction flips sign at
# the early rungs — so the floors admit a sell on a slightly thinner
# sample (3 vs 4) and a single positive early rung (the gate itself),
# while buys keep the stricter bars. The gate bar stays 0.75% BOTH
# sides: lowering it was measured to add almost nothing (sells die at
# the floors/risk, not the gate) and a vol-normalized sell gate was
# rejected for the same reason (study_sell_relax.py).

BUY_SAMPLE_FLOOR = 4
SELL_SAMPLE_FLOOR = 3
SIGNAL_STABILITY_RUNGS: tuple[int, ...] = (0, 1, 2)
BUY_STABILITY_MIN = 2
SELL_STABILITY_MIN = 1

# ---- Mutable scope --------------------------------------------------------------

# The snapshots re-emitted on every run are the MUTABLE SCOPE resolved
# by config.mutable_dates (imported from analysis_forecasts — the
# single source): the ROLLING LATEST snapshot (keyed at the sec_type's
# latest available data date, always refreshed — its long-horizon
# mixed legs complete late) plus the newest completed year-end while
# its 20-trading-day forward windows are still unrealized. An earlier blanket 5-snapshot
# refresh window re-emitted the immutable year-end snapshots on
# every run — retired with the rolling-key migration.

# ---- Stage keys (--metrics) ------------------------------------------------------

STAGE_NAMES = ("rsi", "std", "pairs", "epairs")

# ---- Record layouts ---------------------------------------------------------------

# signal_strategies columns in write order (PK first). ohlcv_type is
# the derived-population cohort verdict (NULL the parent,
# 'ohlcv_boost' / 'ohlcv_selection' the children — NOT a PK member:
# the children's sub_type suffix keeps every cohort's rows PK-distinct
# while the parent's sub_type stays what the forecast ✓ tick join and
# the live tier parse).
STRATEGY_COLUMNS = [
    "code", "sec_type", "signal_type", "signal_sub_type", "ohlcv_type",
    "side", "regime_state",
    "start_date", "end_date",
    "action", "signal_threshold", "signal_delay_days", "confidence",
    "reason", "params",
    "signal_order", "is_active", "is_triggered_once",
]
STRATEGY_PK = [
    "code", "sec_type", "signal_type", "signal_sub_type", "side",
    "regime_state",
    "start_date", "end_date",
]

# history_signals columns in write order (PK first; created_at left to
# its DEFAULT). ohlcv_type mirrors the owning strategy's cohort.
HISTORY_COLUMNS = [
    "code", "sec_type", "signal_type", "signal_sub_type", "ohlcv_type",
    "date", "time",
    "signal_delay_days",
    "action", "signal_excess", "signal_excess_pct", "signal",
    "signal_threshold", "confidence", "is_day_close_trigger",
    "regime_state",
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
    "TABLE_EMISSIONS",
    "SQL_IS_ACTIVE",
    "SQL_SIGNAL_ORDER",
    "SQL_EMISSIONS_DDL",
    "SELL_SIDES",
    "ABOVE_ACTION",
    "BELOW_ACTION",
    "GATE_DIR_AVE_MIN",
    "SIGNAL_DELAY_MAX",
    "BUY_SAMPLE_FLOOR",
    "SELL_SAMPLE_FLOOR",
    "SIGNAL_STABILITY_RUNGS",
    "BUY_STABILITY_MIN",
    "SELL_STABILITY_MIN",
    "RSI_PCTS_EMIT",
    "STD_MA_WINDOW_MIN",
    "STD_K_MIN",
    "PAIRS_SIGNAL_WINDOW_MIN",
    "PAIRS_SIGNAL_SIDES",
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
