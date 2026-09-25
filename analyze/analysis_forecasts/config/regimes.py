"""Market-regime constants (analyze.analysis_forecasts.config.regimes).

The 4-state regime vocabulary shared with stats.market_regimes
(builds.market_regimes) + the per-code self-adaptive WEIGHT fit
constants (the Phase-A study's calibration, docs/market_regimes_
study.md §3/§5).
"""
from __future__ import annotations

# The regime vocabulary — MUST stay in sync with stats.market_regimes'
# CHECK constraint (database/sql/stats/18_market_regimes.sql) and
# builds.market_regimes.config.REGIMES.
REGIME_STATES: tuple[str, ...] = ("calm", "hot", "panic", "quiet")

# Weight-fit constants (recorded per regime_weights row):
#   raw_r  = n-weighted mean of the regime split's MIXED dir_ave over
#            the fit window
#   shrink = N / (N + WEIGHT_SHRINK_N)
#   what_r = GREATEST(raw, 0) * shrink, normalized per (family, code)
#   w_r    = WEIGHT_LAMBDA * what_r + (1 - WEIGHT_LAMBDA) / R
WEIGHT_K_SNAPSHOTS = 5      # fit window length (prior annual snapshots — the 2026-09-22
                            # annual migration; 12 annual snapshots before)
WEIGHT_SHRINK_N = 10.0      # pseudo-count shrinkage toward zero
WEIGHT_LAMBDA = 0.7         # blend of the fitted what_r toward uniform

# The forecast families the weights are fitted for (the
# forecast_identities bucket vocabulary — the former opp_pair family,
# which carried no regime split, was removed 2026-09).
WEIGHT_FAMILIES: tuple[str, ...] = (
    "mov_rsi", "mov_std", "margin_ratio_state",
    "mov_pairs", "mov_pairs_ema", "high_low_streaks",
    "pe_state", "dividend_state",
)

# Weight table (database/sql/analysis/analysis_forecasts/
# 13_regime_weights.sql).
REGIME_WEIGHTS_TABLE = "analysis_forecasts.regime_weights"
