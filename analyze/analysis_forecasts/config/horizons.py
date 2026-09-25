"""Forward horizons and the period vocabulary (config)."""
from __future__ import annotations

# Forward-change horizons (trading days): next-day, 5d, 20d
# (the 60d horizon retired 2026-09-20).
FORWARD_HORIZONS = (1, 5, 20)

# Horizons with max/min forward-change columns
# (5d/20d — the next-day horizon has none).
MM_HORIZONS = (5, 20)

# The swing-aware reversal probability (reverse_prob — P(the n-day
# forward window's adverse path extreme beyond a bar against the side),
# the fixed 1% bar since 2026-09-08, the adaptive k·σ bar before) was
# REMOVED 2026-09-25: it measured only the favorable barrier's touch
# rate (a take-profit hit rate blind to the adverse excursion and to
# the unconditional base rate), so it ranked volatility rather than
# edge. Its columns left forecast_results (reverse_prob / threshold),
# base_rates (base_down_prob / base_up_prob / threshold) and the
# path_low_{n}d / path_high_{n}d window columns with it; the signals
# gate is the sign-aligned dir_ave alone and confidence re-sources to
# dir_ave (see analysis_signals.config).

# ---- Periods ----------------------------------------------------------------
#
# period string for each horizon (n = forward days) plus the weight-blended
# 'mixed' row — the FIXED-weight blend of the three horizon rows (the whole
# forward profile of one trigger as ONE row; the analysis_signals
# confirmation gate reads exactly this row).

PERIOD_MIXED = "mixed"
# period string for each horizon (n = forward days)
PERIOD_FOR_HORIZON: dict[int, str] = {
    1: "next",
    5: "5d",
    20: "20d",
}
ALL_PERIODS: tuple[str, ...] = ("next", "5d", "20d", "mixed")

# The mixed row's FIXED horizon weights (horizon n → weight; 5d 0.65 /
# next 0.25 / 20d 0.10 — the 2026-09-20 rebalance that retired the 60d
# leg, formerly 5d 0.50 / next 0.30 / 20d 0.15 / 60d 0.05). Weights are
# renormalized over the horizons whose stats exist when blending
# (_dfengine's mixed-row expansion / compute_base.compute_base_rate_rows —
# mirrors the idempotent mixed-row backfills in
# database/sql/analysis/analysis_forecasts/01+04).
MIXED_HORIZON_WEIGHTS: dict[int, float] = {
    1: 0.25,   # next
    5: 0.65,   # 5d
    20: 0.10,  # 20d
}
