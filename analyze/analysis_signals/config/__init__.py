"""Configuration for analyze.analysis_signals — the cross-family
constants here plus the mov_rsi family file (config/mov_rsi).

Tables, THE gate constants (the plain blended-row rule, identical for
every family), the confidence-scale defaults, the refresh window and
the write column/PK layouts. The snapshot grid + horizon constants
were PORTED from the retired analyze.analysis_forecasts config when
the forecast layer was removed (2026-10-05) — the signals tier owns
its grid, windows and the RSI window vocabulary (re-exported from
mov_ave_spread's rsi step, the owner of analysis.mov_ave_rsi).
"""
from __future__ import annotations

import datetime

from .grid import (  # noqa: F401
    SEC_TYPES,
    SEC_TYPE_IDENTITY_TABLE,
    N_YEARS,
    WINDOW_YEARS,
    LOOKBACK_PERIOD,
    window_lower,
    mutable_dates,
    build_stat_specs,
)
from .horizons import (  # noqa: F401
    FORWARD_HORIZONS,
    PERIOD_FOR_HORIZON,
    MIXED_HORIZON_WEIGHTS,
)
from .mov_rsi import RSI_PCTS_EMIT, RSI_SIDES, RSI_WINDOWS

from _common.labels import ACTION_CODE, SIDE_CODE  # noqa: F401 (re-exported)

# ---- Tables -------------------------------------------------------------------

TABLE_THRESHOLDS = "analysis_signals.signal_thresholds"
TABLE_HISTORY = "analysis_signals.history_signals"
TABLE_CURVES = "analysis_signals.signal_curves"

# ---- The continuous depth-response curve (mov_rsi curve/) -------------------
#
# The 2026-10 RSI depth-response study's formulation, emitted beside
# the thresholds (no gate — the confidence MODEL, not a strategy):
# per (code, rsi_window, side, regime_state, snapshot) the line
# mu(s) = mu_center + slope * (s - s_center) over the Gaussian depth
# score s = Phi^-1(1 - q) of the trigger day's tail probability. See
# curve/_curves.py and the signal_curves block in 01_signals.sql.

# The tail population the curve is fitted over (tail probability bound).
CURVE_TAIL_Q = 0.10
# The denormalized tail bars' pct grid (the s -> RSI-value mapping).
CURVE_TAIL_PCTS = (1, 2, 3, 5, 10)
# A curve cell's minimum delay-0 trigger count over the window (the
# fit's sample floor — aligns with BUY_SAMPLE_FLOOR).
CURVE_MIN_TRIGGERS = 4

# signal_curves columns in write order (PK first; created_at left to
# its DEFAULT).
CURVE_COLUMNS = [
    "code", "sec_type", "signal_type", "rsi_window",
    "side", "regime_state",
    "start_date", "end_date",
    "n", "mu_center", "s_center", "slope", "tail_bars",
]

# ---- Sides & actions (SMALLINT label codes — the label-code contract) -----
#
# side / action / regime_state ride cudf frames and the DB columns as
# their stats.label_dim SMALLINT codes end-to-end (strings never enter a
# compute frame — the cuDF 26.08 string host-transfer fault class);
# decode only at the API boundary / for display. The sides whose breach
# is an UPWARD crossing (the live tier's ABOVE action): top extremes →
# sell (1 — the measured reversal is downward). Every other side → buy
# (0). Single source for the engines' action mapping.

SELL_SIDE_CODES = (SIDE_CODE["top"],)          # (0,)'s sell side: top
ABOVE_ACTION_CODE = ACTION_CODE["sell"]        # 1
BELOW_ACTION_CODE = ACTION_CODE["buy"]         # 0

# ---- The gate (the plain blended-row rule, every family) ----------------
#
# On the bucket's blended MIXED row (the FIXED-weight blend of the
# next/5d/20d horizon means — 5d 0.65 / next 0.25 / 20d 0.10, computed
# in-engine over the triggers' valid anchors): the sign-aligned blended
# mean forward change (dir_ave — top negated, bottom as-is; the sign
# alignment happens in the engine) > 0.75%.
#
# confidence_base = dir_ave — the bucket's sign-aligned blended mean
# forward change (the expected favorable move ON the bar). Breach
# records denormalize the SCALED confidence: ROUND(10000 x
# confidence_base x scale(|excess|)) integer basis points — see the
# confidence-scale block below.

GATE_DIR_AVE_MIN = 0.0075

# ---- Entry timing -------------------------------------------------------------
#
# The delay ladder (anchors at delays 0..5 + the optimal-entry argmax
# + the ladder-stability floor) was REMOVED 2026-10-05 at the user's
# direction: a strategy IS its delay-0 profile — the rung over each
# qualifying streak's FIRST day (the moment the signal becomes
# observable). signal_delay_days stays in the history schema, written 0.

# ---- The confidence scale (the breach-depth amplifier) --------------------
#
# A threshold row carries the scale model the breach depth applies to
# confidence_base (the on-bar sign-aligned dir_ave):
#
#     scale(|excess|) = scale_base + scale_factor * |excess|   -- linear
#     confidence(bps) = ROUND(10000 * confidence_base * scale)
#
# SCALE_METHOD / SCALE_BASE / SCALE_FACTOR are the values every emitted
# row is STAMPED with (the per-row columns exist so a row can carry a
# tuned model without a code change; the readers — the history writer
# here, live.live_signals' evaluator — interpret the ROW's values).
# scale_base anchors the curve at zero excess (the signal exactly on
# the bar → exactly its base confidence); the linear slope amplifies
# confidence as the breach deepens (the more extreme the RSI beyond
# its percentile tail, the stronger the measured reversal). Negative
# scales clamp to 0 — a pathological factor must never invert the
# confidence. (scale_method 'linear' is the only method the build
# stamps; (linear, log, exponential) are reserved. Readers fall back
# to scale_base on an unknown method — never invent a curve.)

SCALE_METHOD_DEFAULT = "linear"
SCALE_BASE_DEFAULT = 1.0
SCALE_FACTOR_DEFAULT = 0.05

# ---- The robustness floors (the 2026-09 quality-v2 calibration) ---------
#
# Applied at CANDIDACY in detect (the plain gate), they kill the
# small-sample cells the gate alone would register:
#
#   - *_SAMPLE_FLOOR: the bucket's occurrence_count minimum — the
#     number of 10-year-window qualifying streaks behind the stats.
#     Below it the gate's dir_ave is selection noise (winner's bias).
#
# Per-SIDE calibration (2026-09 v2.1 fine-tune, measured in
# temp_scripts/study_sell_floors.py): a SELL's evidence is structurally
# thinner in positive-drift markets — overbought extremes continue
# often enough that the persistence-conditioned direction flips sign at
# the early rungs — so the floors admit a sell on a slightly thinner
# sample (3 vs 4), while buys keep the stricter bar. The gate bar stays
# 0.75% BOTH sides: lowering it was measured to add almost nothing
# (sells die at the floors/risk, not the gate) and a vol-normalized
# sell gate was rejected for the same reason (study_sell_relax.py).

BUY_SAMPLE_FLOOR = 4
SELL_SAMPLE_FLOOR = 3

# ---- Stage keys (--metrics) ------------------------------------------------------

STAGE_NAMES = ("rsi",)

# ---- Record layouts ---------------------------------------------------------------

# signal_thresholds columns in write order (PK first). ohlcv_type is
# the retired derived-population cohort verdict — always NULL now (the
# column stays for the schema; NOT a PK member).
THRESHOLD_COLUMNS = [
    "code", "sec_type", "signal_type", "signal_sub_type", "ohlcv_type",
    "side", "regime_state",
    "start_date", "end_date",
    "action", "signal_threshold",
    "scale_method", "scale_base", "scale_factor", "confidence_base",
    "reason",
    "is_active",
]
THRESHOLD_PK = [
    "code", "sec_type", "signal_type", "signal_sub_type", "side",
    "regime_state",
    "start_date", "end_date",
]

# history_signals columns in write order (PK first; created_at left to
# its DEFAULT). ohlcv_type mirrors the owning strategy's (always NULL).
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
    "TABLE_THRESHOLDS",
    "TABLE_HISTORY",
    "TABLE_CURVES",
    "CURVE_TAIL_Q",
    "CURVE_TAIL_PCTS",
    "CURVE_MIN_TRIGGERS",
    "CURVE_COLUMNS",
    "SELL_SIDE_CODES",
    "ABOVE_ACTION_CODE",
    "BELOW_ACTION_CODE",
    "SIDE_CODE",
    "ACTION_CODE",
    "GATE_DIR_AVE_MIN",
    "SCALE_METHOD_DEFAULT",
    "SCALE_BASE_DEFAULT",
    "SCALE_FACTOR_DEFAULT",
    "BUY_SAMPLE_FLOOR",
    "SELL_SAMPLE_FLOOR",
    "RSI_PCTS_EMIT",
    "STAGE_NAMES",
    "THRESHOLD_COLUMNS",
    "THRESHOLD_PK",
    "HISTORY_COLUMNS",
    "HISTORY_PK",
    "HISTORY_TIME",
    "SIGNAL_SCALE",
    "THRESHOLD_SCALE",
    "EXCESS_SCALE",
    "EXCESS_PCT_SCALE",
    "SEC_TYPES",
    "SEC_TYPE_IDENTITY_TABLE",
    "N_YEARS",
    "WINDOW_YEARS",
    "LOOKBACK_PERIOD",
    "window_lower",
    "mutable_dates",
    "build_stat_specs",
    "FORWARD_HORIZONS",
    "PERIOD_FOR_HORIZON",
    "MIXED_HORIZON_WEIGHTS",
    "RSI_WINDOWS",
    "RSI_SIDES",
]
