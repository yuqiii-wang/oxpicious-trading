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
    "precomputed options wall levels. Single wall type: zone "
    "(strength-scored OI wall ZONE with lifecycle: strikes with OI >=2% of "
    "chain OI clustered into adjacent-strike zones (<=2 strike intervals "
    "apart); dominant zone per side with wall_low/wall_high/wall_center in "
    "raw strike units, mass_share (zone OI / chain OI, eligible >=0.06), "
    "gap_pct (signed center-vs-spot distance), lifecycle state machine "
    "(ACTIVE / ERODED / BREACHED) with day-over-day >=50% strike-range "
    "overlap persistence (days_persisted), and strength_score = mass_share "
    "* exp(-max(gap_pct,0)/8) * (1 + 0.25*min(days_persisted,20)/20)). "
    "FK -> analysis.options_expiry_identity."
)

WALLS_RESULT_COLUMNS = [
    "date", "option_type", "underlying_code", "expiry_date", "wall_type",
    "wall_strike", "wall_oi", "mean_oi", "threshold",
    # wall_type='zone' only (raw strike units — same scale as
    # stats.options_strike / stats.options_settlement.underlying_close):
    "wall_low", "wall_high", "wall_center",
    "mass_share", "gap_pct", "days_persisted", "state", "strength_score",
]

WALLS_NUMERIC_COLS = [
    "wall_strike",
    "wall_oi",
    "mean_oi",
    "threshold",
    "wall_low",
    "wall_high",
    "wall_center",
    "mass_share",
    "gap_pct",
    "days_persisted",
    "strength_score",
]

# Wall type constants
WALL_TYPE_ZONE = "zone"
WALL_TYPES = [WALL_TYPE_ZONE]

# Zone lifecycle states (analysis.options_walls.state CHECK)
WALL_STATE_ACTIVE = "ACTIVE"
WALL_STATE_ERODED = "ERODED"
WALL_STATE_BREACHED = "BREACHED"

# ---- Zone walls (wall_type='zone') ---------------------------------------
# Empirically calibrated on 4,115 (date, nearest-expiry) observations
# 2020-2026 (see temps/wall_v2_*.sql studies):
#   - a strike joins a zone only if its side OI >= 2% of the chain's
#     total (call+put) OI — below that, strikes are fringe noise;
#   - a zone is eligible (emitted) only if its mass share >= 6% of
#     chain OI — at equal strike distance a big call wall holds ~20pp
#     better than a small one (71.9% vs 51.6% at 0.5-2.5% gap);
#   - zones merge across <=2 empty strike intervals (a wall is a
#     massif, not a point);
#   - persistence: a zone that existed the previous trading day (>=50%
#     strike-range overlap) holds +3..+11pp better at equal distance.
ZONE_MIN_STRIKE_MASS = 0.02      # strike-side OI >= 2% of chain OI to enter a zone
ZONE_MERGE_MAX_GAPS = 2          # merge adjacent selected strikes <=2 empty intervals apart
ZONE_ELIGIBLE_MASS_SHARE = 0.06  # emit zone only if mass share >= 6% of chain OI
ZONE_MAX_GAP_PCT = 15.0          # eligible only within +/-15% of spot
ZONE_ERODE_RATIO = 0.70          # ERODED when mass < 70% of the previous day's zone mass
ZONE_PERSIST_MATCH = 0.50        # >=50% strike-range overlap = same zone day-over-day
ZONE_GAP_DECAY = 8.0             # strength decay constant (% gap); matches the hold curve
ZONE_PERSIST_WINDOW = 20         # days_persisted capped in the strength bonus
ZONE_PERSIST_BONUS = 0.25        # max persistence bonus (+25% at 20 days)

# Price scale: raw strike_price is in 厘, divide by 10000 for yuan
PRICE_SCALE = 10000
