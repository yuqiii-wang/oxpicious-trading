"""Configuration for analysis.analysis_signals — the cross-family
constants here plus the mov_rsi family file (config/mov_rsi).

Tables, THE gate constants (the plain blended-row rule, identical for
every family), the confidence-scale defaults, the refresh window and
the write column/PK layouts. The snapshot grid + horizon constants
were PORTED from the retired analysis.analysis_forecasts config when
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
from .mov_rsi import (
    RSI_PCTS_EMIT,
    RSI_REENTRY_EXIT_DAYS,
    RSI_SIDES,
    RSI_WINDOWS,
)
from .boll_std import (
    BOLL_CURVE_FIT_S_GRID,
    BOLL_CURVE_DEEP_ARM_S,
    BOLL_SCALE_FACTOR_DEFAULT,
    BOLL_SIGMA_FLOOR,
    BOLL_STDS_EMIT,
    BOLL_SIDES,
    BOLL_TYPICAL_BAND_S,
    BOLL_WINDOWS,
    boll_sub_type,
    format_std,
)

from _common.labels import ACTION_CODE, SIDE_CODE  # noqa: F401 (re-exported)

# ---- Tables -------------------------------------------------------------------

TABLE_THRESHOLDS = "analysis_signals.signal_thresholds"
TABLE_HISTORY = "analysis_signals.history_signals"
TABLE_CURVES = "analysis_signals.signal_curves"

# ---- The continuous depth-response curve (mov_rsi curve/) -------------------
#
# The 2026-10 RSI depth-response study's formulation, emitted beside
# the thresholds (no gate — the confidence MODEL, not a strategy):
# per (code, audit_window, side, regime_state, snapshot) curve(s) =
# mu_center + interp(fit_curve, s) over the Gaussian depth score
# s = Phi^-1(1 - q) of the day's tail probability. The 2026-10-06
# tails change: the fit population is EVERY tail-episode day
# (mov_ave_rsi.typical_ep_size_{W}days non-NULL — registrations AND
# the gap-1 continuations), each day weighted 1/episode_size (the fit
# counts EPISODES, not days; registration-only populations sit at the
# band edge by construction and leave the deep tail unmeasured). The
# shape is the pooled (index_class, side, regime) WEIGHTED ISOTONIC
# (the 2026-10-06 study: the response is a deep-tail reversion STEP at
# q <= 1-2% over a shallow plateau — a probit-linear fit smears the
# step and extrapolates the unmeasured deep half). See curve/_curves.py
# and the signal_curves block in 01_signals.sql.

# (CURVE_TAIL_Q retired 2026-10-06: band membership moved to the rsi
# analysis layer — mov_ave_rsi.is_typical_{W}days, computed by
# mov_ave_spread steps/rsi_typical.py at the 25% full-history band.
# The 2026-10-06 episode population consumes that layer's
# typical_ep_size_{W}days the same way.)
# The denormalized tail bars' pct grid (the s -> RSI-value mapping) —
# mirrors the fit population: bars at every grid pct inside the band.
CURVE_TAIL_PCTS = (1, 2, 3, 5, 10, 15, 20, 25)
# A curve cell's minimum in-band day count over the window (the emit
# floor — aligns with BUY_SAMPLE_FLOOR).
CURVE_MIN_TRIGGERS = 4
# The pooled (index_class, side, regime) shape's significance gate: the
# weighted Spearman's cluster-adjusted t (t = rho * sqrt((n_ep - 2) /
# (1 - rho^2)), n_ep = the pool's EPISODE count — within-episode days
# are correlated, so the effective sample is episodes). A
# non-significant shape is band noise, not a depth response — those
# cells store fit_curve flat (all-zero offsets; the cell draws at its
# mu_center). The pool is the code's OWN stats.sec_classification
# industry_id (the 2026-10-06 per-index-class refinement: the
# universe-wide pool borrowed gradients across unlike families — the
# 10 big indices' sell x hot is flat/reversed, rho=-0.011 and -0.356
# for 000001, while the universe-wide slope looked significant at
# t=+39.7; 2026-10-07: the key moved from the L1 sector_id to the L2
# industry_id — pool within the industry, not the sector). The buy
# side keeps the retired linear fit's >= 0 guard: a
# NEGATIVE buy rho stores the flat payload (the response runs against
# the side); sell shapes follow the pool's rho sign.
CURVE_SLOPE_MIN_T = 2.0
# The stored fit_curve's s grid (lo, hi, step): the pooled isotonic
# step function sampled for the JSON payload — consumers interpolate
# offsets linearly between grid points. Covers the drawn tail (the 25%
# band edge 0.6745 .. past the 1% band center 2.58).
CURVE_FIT_S_GRID = (0.66, 2.62, 0.02)
# The curve pooling class for codes without a
# stats.sec_classification row (they pool together and their shape
# stands or falls on its own significance).
UNCLASSIFIED_CLASS = "unclassified"
# The per-CODE pool's minimum EPISODES before its own fit is trusted
# (2026-10-07 two-tier fit: the class pooling exists ONLY to buy
# statistical significance for thin codes, so it stays OPTIONAL — a
# code whose own (windows x days) pool carries >= this many episodes
# AND clears CURVE_SLOPE_MIN_T on its own fits ITS OWN shape and never
# borrows the industry-class gradient; below it the
# (industry_class, side, regime, pass) pool is the fallback, exactly
# the pre-2026-10-07 behavior). Aligns with CURVE_MIN_TRIGGERS.
CURVE_OWN_POOL_MIN_EPISODES = 4
# (CURVE_REENTRY_EXIT_DAYS retired 2026-10-06: the re-entry
# invalidation window lives at the rsi analysis layer —
# mov_ave_spread steps/rsi_typical.py RSI_REENTRY_EXIT_DAYS = 5.)

# signal_curves columns in write order (PK first; created_at left to
# its DEFAULT). is_typical is the cell's episode-PASS population (PK
# member, the recovered boolean — replaces the 0/1/2 population
# smallint 2026-10-07), SIDE-MIRRORED (the amplified tail event
# differs by side): TRUE = buy side the ISOLATED first-touch passes'
# days (typical — e.g. RSI isolated ups/downs; an episode starting >
# 20 trading days after the previous same-side band day — the
# capitulation) / sell side the RE-TEST passes' days (a fresh episode
# whose previous overbought episode ended <= 20 days ago — the double
# top), FALSE = the re-entry passes' days (flicker re-qualifications,
# falling knives, isolated breakouts — the noisy population);
# is_ohlcv_boosted
# is the population VARIANT (PK member): FALSE = the plain
# episode-weighted fit, TRUE = the boost twin (the same days, episode
# x ohlcv-score weight-blended).
# The mu_{n}d columns are the horizon path beside the blend (NULLable
# — a leg is NULL when no day carries it); fit_curve is the pooled
# isotonic shape JSON (gated by fit_t), s_lo/s_hi the cell's MEASURED
# s range (draw the fit only inside it — no extrapolation); band_curve
# is the cell's EMPIRICAL 1%-stride depth response over the whole tail.
CURVE_COLUMNS = [
    "code", "sec_type", "signal_type", "audit_window",
    "side", "regime_state",
    "start_date", "end_date", "is_ohlcv_boosted", "is_typical",
    "n", "n_episodes", "mu_center", "s_center",
    "fit_curve", "fit_rho", "fit_t", "s_lo", "s_hi",
    "mu_1d", "mu_3d", "mu_5d", "mu_10d", "mu_20d",
    "tail_bars", "band_curve",
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
# alignment happens in the engine) > the side's gate bar below.
#
# confidence_base = dir_ave — the bucket's sign-aligned blended mean
# forward change (the expected favorable move ON the bar). Breach
# records denormalize the SCALED confidence: ROUND(10000 x
# confidence_base x scale(|excess|)) integer basis points — see the
# confidence-scale block below.

GATE_DIR_AVE_MIN = 0.0075

# The SELL side's gate bar (buys keep GATE_DIR_AVE_MIN): 0 — a sell
# needs ANY measured reversal edge (the average forward move after the
# overbought extreme must be a decline), not a buy-sized 0.75% edge.
# Per-side since 2026-10-06: overbought extremes are MOMENTUM for
# drift-up names (000001's top cells average a sign-aligned −0.5%), so
# the flat 0.75% bar withheld nearly every index sell while the buy
# side cleared it routinely — the bar tested the wrong hypothesis on
# the sell side, where the signal's job is exit timing, not short
# entry. dir_ave > 0 still guarantees positive expectancy — the gate
# admits a thin edge, never an expected loser. (Supersedes the
# 2026-09 note that lowering the bar adds nothing: that study measured
# the retired forecast-era funnel — analysis_forecasts tables, the
# claim-profile gate and the delay ladder, all removed 2026-10-05;
# under the current funnel the sell bar re-opens.)
GATE_DIR_AVE_MIN_SELL = 0.0

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
# sample (3 vs 4), while buys keep the stricter bar. (The gate bar is
# per-side too — see GATE_DIR_AVE_MIN_SELL above.)

BUY_SAMPLE_FLOOR = 4
SELL_SAMPLE_FLOOR = 3

# ---- Stage keys (--metrics) ------------------------------------------------------

STAGE_NAMES = ("rsi", "boll")

# ---- Record layouts ---------------------------------------------------------------

# signal_thresholds columns in write order (PK first). is_ohlcv_boosted
# is the bucket's ohlcv-boost verdict (engines/ohlcv_boost) — an
# analysis marker, NOT a PK member, never a gate.
THRESHOLD_COLUMNS = [
    "code", "sec_type", "signal_type", "signal_sub_type",
    "is_ohlcv_boosted",
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
# its DEFAULT). is_ohlcv_boosted is the event's population VARIANT (PK
# member): FALSE = the plain event, TRUE = the boost twin (the same
# trigger, w-scaled confidence — engines/ohlcv_boost). is_typical is
# the event's episode-PASS ATTRIBUTE (non-PK; the trigger day's own
# window's is_typical flag — TRUE the typical pass, FALSE the re-entry
# pass; the UI history pass toggle filters on it).
HISTORY_COLUMNS = [
    "code", "sec_type", "signal_type", "signal_sub_type",
    "is_ohlcv_boosted",
    "date", "time",
    "signal_delay_days",
    "action", "signal_excess", "signal_excess_pct", "signal",
    "signal_threshold", "confidence", "is_day_close_trigger",
    "regime_state", "is_typical",
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
    "CURVE_TAIL_PCTS",
    "CURVE_MIN_TRIGGERS",
    "CURVE_SLOPE_MIN_T",
    "CURVE_FIT_S_GRID",
    "CURVE_OWN_POOL_MIN_EPISODES",
    "UNCLASSIFIED_CLASS",
    "CURVE_COLUMNS",
    "SELL_SIDE_CODES",
    "ABOVE_ACTION_CODE",
    "BELOW_ACTION_CODE",
    "SIDE_CODE",
    "ACTION_CODE",
    "GATE_DIR_AVE_MIN",
    "GATE_DIR_AVE_MIN_SELL",
    "SCALE_METHOD_DEFAULT",
    "SCALE_BASE_DEFAULT",
    "SCALE_FACTOR_DEFAULT",
    "BUY_SAMPLE_FLOOR",
    "SELL_SAMPLE_FLOOR",
    "RSI_PCTS_EMIT",
    "BOLL_STDS_EMIT",
    "BOLL_SIDES",
    "BOLL_WINDOWS",
    "BOLL_TYPICAL_BAND_S",
    "BOLL_CURVE_FIT_S_GRID",
    "BOLL_CURVE_DEEP_ARM_S",
    "BOLL_SCALE_FACTOR_DEFAULT",
    "BOLL_SIGMA_FLOOR",
    "boll_sub_type",
    "format_std",
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
    "RSI_REENTRY_EXIT_DAYS",
]
