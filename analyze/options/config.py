"""Configuration constants for analyze.options.

Centralizes the target table, validity thresholds, and column definitions
so the pipeline modules share a single source of truth. Mirrors the
Volatility Smile panel logic (data_viz .../szse-options/VolSmilePanel.tsx).
"""
from __future__ import annotations

# ---- Target table (analysis schema) ---------------------------------------
TABLE_NAME = "analysis.options_stats_before_expiry"
ANALYSIS_NAME = "options_stats_before_expiry"

DESCRIPTION = (
    "Per-(date, option_type, underlying_code, expiry_date) store of precomputed "
    "options rolling skew statistics (FK -> analysis.options_expiry_identity), "
    "so the frontend/API can join per expiry group instead of recomputing "
    "online. Stats are computed per expiry set "
    "(date, option_type, underlying_code, expiry_date): S* = "
    "(underlying_close/1000) * E[M], E[M] = sum(max(1,OI)*strike/S) / "
    "sum(max(1,OI)) over the set's valid-IV contracts "
    "(>=3 rows). Stores three gaps (direction: today - reference): "
    "today_gap_from_today_spot = S* - spot; today_gap_from_max/"
    "min_before_expiry = S* - max/min(S* over the future window "
    "[date, expiry]) - computed only for matured expiries (NULL "
    "otherwise). Also stores max_date/min_date_before_expiry - the "
    "dates within the future window when skew_price reached its "
    "max/min (NULL for non-matured expiries). Built by analyze.options "
    "(--force = DELETE + chunked COPY; default = incremental "
    "missing-expiry-group upsert + NULL-gap backfill on "
    "maturing); all INSERTs in Python per project rule."
)

# ---- Validity thresholds (must match VolSmilePanel.tsx) -------------------
# implied_vol strictly inside (IV_MIN, IV_MAX) — panel filter is
# `iv != null && iv > 0 && iv < 5`.
IV_MIN = 0.0
IV_MAX = 5.0

# Minimum valid contracts per (date, underlying, expiry) group for a
# non-NULL skew value (panel: `if (valid.length < 3) ... null`).
MIN_CONTRACTS = 3

# ---- Price scale -----------------------------------------------------------
# strike_price / underlying_close are stored in 厘 (1/1000 yuan or index
# points). Moneyness = strike/underlying_close is scale-free; the stored
# gaps are in yuan/index points after dividing by PRICE_SCALE.
PRICE_SCALE = 1000.0

# ---- Numeric columns for sanitize_for_db_insert ---------------------------
NUMERIC_COLS = [
    "today_gap_from_today_spot",
    "today_gap_from_max_before_expiry",
    "today_gap_from_min_before_expiry",
]

# ---- Options stats_before_expiry result columns (expiry-level) ------------
STATS_BEFORE_EXPIRY_RESULT_COLUMNS = [
    "date", "option_type", "underlying_code", "expiry_date",
    "today_gap_from_today_spot",
    "today_gap_from_max_before_expiry",
    "today_gap_from_min_before_expiry",
    "max_date_before_expiry",
    "min_date_before_expiry",
]

# ---- Options expiry identity table ----------------------------------------
# Lookup table for (date, option_type, underlying_code, expiry_date) groups.
EXPIRY_IDENTITY_TABLE = "analysis.options_expiry_identity"

# PK columns for expiry-level tables (skewness_stats, oi_stats).
EXPIRY_PK_COLUMNS = ["date", "option_type", "underlying_code", "expiry_date"]

# ---- Options skewness stats table ------------------------------------------
# Per-(date, option_type, underlying_code, expiry_date) rolling skewness
# (moneyness) statistics aggregated at the expiry-group level.
# "Skewness" = OI-weighted mean moneyness = OI-weighted(strike_price / underlying_close).
SKEWNESS_TABLE_NAME = "analysis.options_skewness_stats"
SKEWNESS_ANALYSIS_NAME = "options_skewness_stats"

SKEWNESS_DESCRIPTION = (
    "Per-(date, option_type, underlying_code, expiry_date) store of precomputed "
    "rolling skewness statistics for option expiry groups. 'Skewness' = OI-weighted "
    "mean moneyness (strike_price / underlying_close) across all valid contracts "
    "of an expiry group. Rolling windows (5/20/60 days) compute MA, STD, "
    "gap-from-spot (skewness_MA - 1), linear regression slope of gap, and "
    "whole-period cumulative correlation with spot (and spot MA). For open "
    "(non-matured) expiry groups, expiry_date is set to the mean of all "
    "expiry dates per (option_type, underlying_code). FK -> "
    "analysis.options_expiry_identity. Built by analyze.options."
)

SKEWNESS_RESULT_COLUMNS = [
    "date", "option_type", "underlying_code", "expiry_date",
    "skewness_ma5", "skewness_ma20", "skewness_ma60",
    "skewness_std5", "skewness_std20", "skewness_std60",
    "gap_skewness_vs_spot_ma5", "gap_skewness_vs_spot_ma20",
    "gap_skewness_vs_spot_ma60",
    "gap_skewness_vs_spot_slope",
    "gap_skewness_vs_spot_ma5_slope", "gap_skewness_vs_spot_ma20_slope",
    "gap_skewness_vs_spot_ma60_slope",
    "corr_skewness_ma5_vs_spot_ma5",
    "corr_skewness_ma20_vs_spot_ma20",
    "corr_skewness_ma60_vs_spot_ma60",
]

SKEWNESS_NUMERIC_COLS = [
    "skewness_ma5", "skewness_ma20", "skewness_ma60",
    "skewness_std5", "skewness_std20", "skewness_std60",
    "gap_skewness_vs_spot_ma5", "gap_skewness_vs_spot_ma20",
    "gap_skewness_vs_spot_ma60",
    "gap_skewness_vs_spot_slope",
    "gap_skewness_vs_spot_ma5_slope", "gap_skewness_vs_spot_ma20_slope",
    "gap_skewness_vs_spot_ma60_slope",
    "corr_skewness_ma5_vs_spot_ma5",
    "corr_skewness_ma20_vs_spot_ma20",
    "corr_skewness_ma60_vs_spot_ma60",
]

SKEWNESS_WINDOWS = [5, 20, 60]

# ---- Options OI stats table ------------------------------------------------
OI_TABLE_NAME = "analysis.options_oi_stats"
OI_ANALYSIS_NAME = "options_oi_stats"

OI_DESCRIPTION = (
    "Per-(date, option_type, underlying_code, expiry_date) store of precomputed "
    "options OI-related statistics for expiry groups. Stores MA5/MA20/MA60 "
    "whole-period cumulative correlation between put/call OI ratio and "
    "underlying spot price. FK -> analysis.options_expiry_identity."
)

OI_RESULT_COLUMNS = [
    "date", "option_type", "underlying_code", "expiry_date",
    "corr_put_call_ratio_vs_spot_ma5",
    "corr_put_call_ratio_vs_spot_ma20",
    "corr_put_call_ratio_vs_spot_ma60",
]

OI_NUMERIC_COLS = [
    "corr_put_call_ratio_vs_spot_ma5",
    "corr_put_call_ratio_vs_spot_ma20",
    "corr_put_call_ratio_vs_spot_ma60",
]
