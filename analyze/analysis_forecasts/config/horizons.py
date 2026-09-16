"""Forward horizons, reversal bars and the period vocabulary (config)."""
from __future__ import annotations

# Forward-change horizons (trading days): next-day, 5d, 20d, 60d.
FORWARD_HORIZONS = (1, 5, 20, 60)

# Horizons with max/min forward-change + max_low_change_ratio columns
# (5d/20d/60d — the next-day horizon has none).
MM_HORIZONS = (5, 20, 60)

# A forward move beyond this (fractional) threshold against the bucket
# side counts as a reversal — measured SWING-AWARE on the n-day
# forward window's ADVERSE PATH EXTREME (path_low_{n}d = the lowest
# close of (t, t+n] vs the signal close for top/upper, path_high_{n}d
# = the highest for bottom/lower): at n = 5 the reversal event is the
# window swinging 1% beyond the signal-day close AT ANY of the 5
# closes, not merely at the period-end (the path extreme dominates the
# endpoint, so the endpoint event is the strictly weaker subset). The
# bar is FIXED (mode "fixed", 2026-09-08): every reverse_prob reads as
# a plain "P > 1%" — P(the period swings ≥ 1% against the side) — one
# scale for every code and horizon.
REVERSE_THRESHOLD = 0.01

# ---- Previous adaptive bar ("std" mode, study 2026-09; DISABLED) ------------
#
# The alternative reverse_threshold(code, month, n) = k_n · σ(code,
# month, n), σ = population std of the n-day forward changes over ALL
# of the code's trailing-window days (recomputed every stat month — the
# window ends at the stat month, so no look-ahead). Its k per horizon
# was selected by temp_scripts/study_reverse_threshold.py (rolling M-1
# P90 gate OOS on index/etf/stock): at t = k·σ the no-edge reversal
# rate is Φ(−k) at EVERY horizon, which de-saturates the 20d/60d
# reverse_probs the fixed 1% bar pins at ~1.0 (its σ-equivalent shrinks
# from ~0.7σ at the next-day horizon to ~0.1σ at 60d) and makes the
# cross-period MAX(reverse_prob) confidence comparable. OOS gated
# dir_ave vs the fixed bar at the gate's P90: 20d +10/+14/+19%,
# 60d +16/+15/+29% (index/etf/stock), short horizons ~neutral. k = 2
# degenerates (dead share > 55%). DISABLED 2026-09-08 in favour of the
# interpretable fixed bar — flip REVERSE_THRESHOLD_MODE back to "std"
# to restore (the constants stay for that purpose).
REVERSE_THRESHOLD_MODE = "fixed"  # "fixed" | "std"
REVERSE_THRESHOLD_STD_K: dict[int, float] = {
    1: 0.5,    # next — the fixed 1% bar's median σ-equivalent (~0.55σ)
    5: 0.75,
    20: 1.0,
    60: 1.0,
}

# Window valid-days below which σ is not trusted (the code falls back to
# the fixed REVERSE_THRESHOLD bar for that month/horizon). The 5y
# full-window gate yields ~1,220 days, so this only bites pathological
# windows.
REVERSE_THRESHOLD_STD_MIN_DAYS = 60

# ---- Periods ----------------------------------------------------------------
#
# period string for each horizon (n = forward days) plus the weight-blended
# 'mixed' row — the FIXED-weight blend of the four horizon rows (the whole
# forward profile of one trigger as ONE row; the analysis_signals
# confirmation gate reads exactly this row).

PERIOD_MIXED = "mixed"
# period string for each horizon (n = forward days)
PERIOD_FOR_HORIZON: dict[int, str] = {
    1: "next",
    5: "5d",
    20: "20d",
    60: "60d",
}
ALL_PERIODS: tuple[str, ...] = ("next", "5d", "20d", "60d", "mixed")

# The mixed row's FIXED horizon weights (horizon n → weight; 5d 0.50 /
# next 0.30 / 20d 0.15 / 60d 0.05). Weights are renormalized over the
# horizons whose stats exist when blending (wide.build_result_rows /
# compute_base.compute_base_rate_rows — mirrors the idempotent mixed-row
# backfills in database/sql/analysis/analysis_forecasts/01+04).
MIXED_HORIZON_WEIGHTS: dict[int, float] = {
    1: 0.30,   # next
    5: 0.50,   # 5d
    20: 0.15,  # 20d
    60: 0.05,  # 60d
}
