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
# Per-(date, option_type, underlying_code, expiry_date, skew_type) rolling
# skewness statistics aggregated at the expiry-group level, for multiple
# data sources separated by skew_type:
#   'oi_moneyness' : OI-weighted mean moneyness
#                   = OI-weighted(strike_price / underlying_close)
#                   (positioning metric)
#   'iv_smile'     : OI-weighted 3rd standardized moment of implied vol
#                   across strikes (pricing metric, from options_greeks)
#   'greek_delta'  : delta-weighted put/call OI ratio (dpcr), pair-level
#   'greek_gamma'  : GEX-style call-minus-put gamma balance, pair-level
#   'greek_vega'   : OTM-wing vega balance, pair-level
SKEWNESS_TABLE_NAME = "analysis.options_skewness_stats"
SKEWNESS_ANALYSIS_NAME = "options_skewness_stats"

SKEW_TYPE_MONEYNESS = "oi_moneyness"
SKEW_TYPE_IV_SMILE = "iv_smile"

# Greeks with an industry-standard positioning-skew metric, stored as a
# skew data source: skew_type = 'greek_<name>'. Each is a PAIR-level
# CALL-vs-PUT contrast computed by its own module under
# analyze/options/compute/ (greek_delta / greek_gamma / greek_vega):
#   delta — delta-weighted put/call OI ratio (PCR refinement)
#   gamma — normalized GEX-style call-minus-put gamma balance
#   vega  — OTM-wing vega balance (open-interest mirror of the 25d RR)
# theta/rho have no standard positioning skew (theta ≈ −½σ²S²Γ/365 is
# collinear with gamma; rho ∝ T is negligible short-dated) and are NOT
# computed.
GREEK_NAMES = ["delta", "gamma", "vega"]
GREEK_SKEW_TYPES = [f"greek_{g}" for g in GREEK_NAMES]

# No-tilt anchor of each greek_* metric (gap = skewness - neutral;
# anchors the cross counts, gap columns and the price rebase):
#   greek_delta: 0.5 (balanced put/call directional book)
#   greek_gamma / greek_vega: 0.0 (balanced call/put wings)
GREEK_NEUTRAL = {"delta": 0.5, "gamma": 0.0, "vega": 0.0}

# Price-space rebase scale for the greek_* skew metrics (correlation
# basis + frontend display): skew_price = S * (1 + (skew - neutral) * k),
# i.e. one full unit of tilt maps to ±10% of spot.
GREEK_SKEW_PRICE_K = 0.10

SKEW_TYPES = [SKEW_TYPE_MONEYNESS, SKEW_TYPE_IV_SMILE] + GREEK_SKEW_TYPES

# PK columns of the skewness stats table (identity FK cols + skew_type).
SKEWNESS_PK_COLUMNS = [
    "date", "option_type", "underlying_code", "expiry_date", "skew_type",
]

SKEWNESS_DESCRIPTION = (
    "Per-(date, option_type, underlying_code, expiry_date, skew_type) store "
    "of precomputed rolling skewness statistics for option expiry groups, "
    "for multiple skew data sources separated by skew_type: oi_moneyness = "
    "OI-weighted mean moneyness (strike_price / underlying_close) — a "
    "positioning metric; iv_smile = OI-weighted 3rd standardized moment of "
    "implied vol (from stats.options_greeks) — a pricing metric; greek_delta "
    "= delta-weighted put/call OI ratio dpcr = Σ_put OI·|Δ| / Σ_all OI·|Δ| "
    "over the whole chain (neutral 0.5 = balanced directional book — the "
    "delta-weighted refinement of the plain put/call ratio); greek_gamma = "
    "normalized GEX-style call-minus-put gamma balance = (Σ_call OI·Γ − "
    "Σ_put OI·Γ)/(Σ_call OI·Γ + Σ_put OI·Γ) over the whole chain (neutral "
    "0; call gamma positive / put gamma negative per the dealer-positioning "
    "sign convention); greek_vega = OTM-wing vega balance = (Σ_C OI·ν − "
    "Σ_P OI·ν)/(Σ_C OI·ν + Σ_P OI·ν) on the 0<|delta|<0.5 wings (neutral 0; "
    "the open-interest mirror of the 25-delta risk reversal). The greek_* "
    "metrics are PAIR-level CALL-vs-PUT contrasts per underlying+expiry — "
    "the CALL and PUT rows of a pair hold the SAME value — weighted by "
    "open_interest with zero OI = zero vote; theta/rho have no "
    "industry-standard positioning skew and are not computed. Stores the "
    "daily raw skewness value (skewness column) plus rolling windows "
    "(5/20/60 days): MA, STD, gap-from-neutral (skewness_MA − neutral; "
    "neutral = 1 for oi_moneyness/iv_smile, 0.5 for greek_delta, 0 for "
    "greek_gamma/greek_vega), linear regression slope of gap, and "
    "whole-period cumulative correlation with spot (and spot MA). "
    "Price-space correlation basis: oi_moneyness/iv_smile use "
    "underlying_close × skewness; greek_* use underlying_close × (1 + "
    "(skewness − neutral) × 0.10). For open (non-matured) expiry groups, "
    "expiry_date is set to the mean of all expiry dates per (option_type, "
    "underlying_code). FK -> analysis.options_expiry_identity. Built by "
    "analyze.options."
)

SKEWNESS_RESULT_COLUMNS = [
    "date", "option_type", "underlying_code", "expiry_date", "skew_type",
    "skewness",
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
    "skewness",
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

# ---- Options IV skew stats table --------------------------------------------
# Per-(date, option_type, underlying_code, expiry_date) implied-volatility
# skew statistics. Unlike options_skewness_stats (OI-weighted mean moneyness
# = a positioning metric), this table is derived from option premiums via
# the implied_vol already stored in stats.options_greeks — a pricing metric.
# All IV / skew values are stored in vol points (percent), e.g. 25.30.
IV_SKEW_TABLE_NAME = "analysis.options_iv_skew_stats"
IV_SKEW_ANALYSIS_NAME = "options_iv_skew_stats"

IV_SKEW_DESCRIPTION = (
    "Per-(date, option_type, underlying_code, expiry_date) store of implied-"
    "volatility skew statistics for option expiry groups, derived from "
    "implied_vol in stats.options_greeks (which is calibrated from option "
    "premiums via Black-76). All IV/skew values are in vol points (percent). "
    "Daily metrics: atm_iv (IV of contract closest to moneyness 1.0), "
    "iv_call25/iv_put25 (IV of OTM contract nearest |delta|=0.25), "
    "risk_reversal_25d = iv_call25 - iv_put25 (negative = puts richer = "
    "downside hedging demand), put_skew_25d = iv_put25 - atm_iv, "
    "call_skew_25d = iv_call25 - atm_iv, smile_skewness (OI-weighted 3rd "
    "standardized moment of IV across strikes). Rolling suite (5/20/60 days) "
    "on risk_reversal_25d: MA, STD, full-history slopes, and expanding "
    "correlation with spot MA. For open (non-matured) expiry groups, "
    "expiry_date is collapsed to the mean of all expiry dates per "
    "(option_type, underlying_code). FK -> analysis.options_expiry_identity. "
    "Built by analyze.options."
)

IV_SKEW_RESULT_COLUMNS = [
    "date", "option_type", "underlying_code", "expiry_date",
    "atm_iv", "iv_call25", "iv_put25",
    "risk_reversal_25d", "put_skew_25d", "call_skew_25d",
    "smile_skewness",
    "rr25_ma5", "rr25_ma20", "rr25_ma60",
    "rr25_std5", "rr25_std20", "rr25_std60",
    "rr25_slope",
    "rr25_ma5_slope", "rr25_ma20_slope", "rr25_ma60_slope",
    "corr_rr25_ma5_vs_spot_ma5",
    "corr_rr25_ma20_vs_spot_ma20",
    "corr_rr25_ma60_vs_spot_ma60",
]

IV_SKEW_NUMERIC_COLS = [
    "atm_iv", "iv_call25", "iv_put25",
    "risk_reversal_25d", "put_skew_25d", "call_skew_25d",
    "smile_skewness",
    "rr25_ma5", "rr25_ma20", "rr25_ma60",
    "rr25_std5", "rr25_std20", "rr25_std60",
    "rr25_slope",
    "rr25_ma5_slope", "rr25_ma20_slope", "rr25_ma60_slope",
    "corr_rr25_ma5_vs_spot_ma5",
    "corr_rr25_ma20_vs_spot_ma20",
    "corr_rr25_ma60_vs_spot_ma60",
]

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

# ---- Options walls table -------------------------------------------------
WALLS_TABLE_NAME = "analysis.options_walls"
WALLS_ANALYSIS_NAME = "options_walls"

WALLS_DESCRIPTION = (
    "Per-(date, option_type, underlying_code, expiry_date, wall_type) store of "
    "precomputed options wall levels. Two wall types: 80pct (boundary where "
    "one side dominates >=80% of total OI at each strike, interpolated across "
    "strikes) and large_num (strike with the max OI among those exceeding 70% "
    "of the mean OI across all strikes in the expiry group). FK -> "
    "analysis.options_expiry_identity."
)

WALLS_RESULT_COLUMNS = [
    "date", "option_type", "underlying_code", "expiry_date", "wall_type",
    "wall_strike", "wall_oi", "mean_oi", "threshold",
]

WALLS_NUMERIC_COLS = [
    "wall_strike",
    "wall_oi",
    "mean_oi",
    "threshold",
]

# Wall type constants
WALL_TYPE_80PCT = "80pct"
WALL_TYPE_LARGE_NUM = "large_num"
WALL_TYPES = [WALL_TYPE_80PCT, WALL_TYPE_LARGE_NUM]

# 80% wall thresholds (matching frontend bandData.ts)
PUT_PCT_RED = 80    # bearish: puts >= 80% of total OI
PUT_PCT_GREEN = 20  # bullish: calls >= 80% of total OI

# Large num wall fraction (matching frontend LARGE_NUM_MEAN_FRACTION)
LARGE_NUM_MEAN_FRACTION = 0.70

# Price scale: raw strike_price is in 厘, divide by 10000 for yuan
PRICE_SCALE = 10000
