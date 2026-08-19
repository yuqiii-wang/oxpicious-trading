"""Configuration constants for analyze.options.

Centralizes column definitions for the pipeline modules.
"""
from __future__ import annotations

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
    "count_skewness_curve_crossed_spot",
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
    "count_skewness_curve_crossed_spot",
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
