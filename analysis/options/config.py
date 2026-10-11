"""Configuration constants for analysis.options.

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
# analysis/options/compute/ (greek_delta / greek_gamma / greek_vega):
#   delta — delta-weighted put/call OI ratio (PCR refinement)
#   gamma — normalized GEX-style call-minus-put gamma balance
#   vega  — OTM-wing vega balance (open-interest mirror of the 25d RR)
# theta/rho have no standard positioning skew (theta ≈ −½σ²S²Γ/365 is
# collinear with gamma; rho ∝ T is negligible short-dated) and are NOT
# computed.
GREEK_NAMES = ["delta", "gamma", "vega"]
GREEK_SKEW_TYPES = [f"greek_{g}" for g in GREEK_NAMES]

# No-tilt anchor of each greek_* metric (gap = skewness - neutral;
# anchors the contrarian metrics and gap columns):
#   greek_delta: 0.5 (balanced put/call directional book)
#   greek_gamma / greek_vega: 0.0 (balanced call/put wings)
GREEK_NEUTRAL = {"delta": 0.5, "gamma": 0.0, "vega": 0.0}

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
    "daily raw skewness value (skewness column) plus pre-expiry contrarian "
    "metrics on the gap (skewness − neutral): cross_count_20d = neutral "
    "crossings in the trailing 20 sessions (contested positioning into "
    "expiry), days_since_last_cross = sessions since the gap last crossed "
    "neutral (freshness of the last flip; 0 = crossed today), and "
    "gap_side_share_20d = share of the trailing 20 sessions at/above "
    "neutral (one-sided crowding, in [0,1]). Plus rolling windows "
    "(5/20/60 days): MA, STD, gap-from-neutral (skewness_MA − neutral; "
    "neutral = 1 for oi_moneyness/iv_smile, 0.5 for greek_delta, 0 for "
    "greek_gamma/greek_vega) and linear regression slope of gap. "
    "For open (non-matured) expiry groups, "
    "expiry_date is set to the mean of all expiry dates per (option_type, "
    "underlying_code). FK -> analysis.options_expiry_identity. Built by "
    "analysis.options."
)

SKEWNESS_RESULT_COLUMNS = [
    "date", "option_type", "underlying_code", "expiry_date", "skew_type",
    "skewness",
    "cross_count_20d", "days_since_last_cross", "gap_side_share_20d",
    "skewness_ma5", "skewness_ma20", "skewness_ma60",
    "skewness_std5", "skewness_std20", "skewness_std60",
    "gap_skewness_vs_spot_ma5", "gap_skewness_vs_spot_ma20",
    "gap_skewness_vs_spot_ma60",
    "gap_skewness_vs_spot_slope",
    "gap_skewness_vs_spot_ma5_slope", "gap_skewness_vs_spot_ma20_slope",
    "gap_skewness_vs_spot_ma60_slope",
]

SKEWNESS_NUMERIC_COLS = [
    "skewness",
    "cross_count_20d", "days_since_last_cross", "gap_side_share_20d",
    "skewness_ma5", "skewness_ma20", "skewness_ma60",
    "skewness_std5", "skewness_std20", "skewness_std60",
    "gap_skewness_vs_spot_ma5", "gap_skewness_vs_spot_ma20",
    "gap_skewness_vs_spot_ma60",
    "gap_skewness_vs_spot_slope",
    "gap_skewness_vs_spot_ma5_slope", "gap_skewness_vs_spot_ma20_slope",
    "gap_skewness_vs_spot_ma60_slope",
]

SKEWNESS_WINDOWS = [5, 20, 60]

# Trailing session window of the pre-expiry contrarian metrics
# (cross_count_20d / gap_side_share_20d) — one monthly expiry cycle.
SKEWNESS_CROSS_WINDOW = 20

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
    "call_skew_25d = iv_call25 - atm_iv, iv_call10/iv_put10 (deeper "
    "|delta|=0.10 wing; NULL where no near-target contract exists on the "
    "strike grid), risk_reversal_10d = iv_call10 - iv_put10, "
    "smile_skewness (OI-weighted 3rd standardized moment of IV across "
    "strikes). Rolling suite (5/20/60 days) "
    "on risk_reversal_25d: MA, STD, full-history slopes, and expanding "
    "correlation with spot MA. For open (non-matured) expiry groups, "
    "expiry_date is collapsed to the mean of all expiry dates per "
    "(option_type, underlying_code). FK -> analysis.options_expiry_identity. "
    "Built by analysis.options."
)

IV_SKEW_RESULT_COLUMNS = [
    "date", "option_type", "underlying_code", "expiry_date",
    "atm_iv", "iv_call25", "iv_put25",
    "risk_reversal_25d", "put_skew_25d", "call_skew_25d",
    "iv_call10", "iv_put10", "risk_reversal_10d",
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
    "iv_call10", "iv_put10", "risk_reversal_10d",
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
# Dedicated per-expiry OI statistics, keyed by the REAL expiry date (NOT
# the collapsed open-group convention — per-expiry tooltips need
# per-contract-expiry OI levels/changes, which the pooled open-group
# collapse cannot represent; hence no FK to options_expiry_identity,
# whose open-group keys are collapsed mean dates). option_type is kept in
# the PK: CALL and PUT rows hold the SAME value (stats pool calls+puts).
OI_TABLE_NAME = "analysis.options_oi_stats"
OI_ANALYSIS_NAME = "options_oi_stats"

OI_DESCRIPTION = (
    "Dedicated per-expiry options OI statistics, keyed by REAL expiry date "
    "(not the collapsed open-group convention; no FK to "
    "options_expiry_identity for that reason). Per (date, option_type, "
    "underlying_code, expiry_date): oi_total = calls+puts open interest of "
    "the expiry group (contracts), oi_delta_5d / oi_delta_20d = change vs "
    "5/20 trading sessions earlier (session offsets index the underlying's "
    "option calendar; NULL when the group had no row at the offset "
    "session), oi_max_20d = max oi_total over the trailing 20 sessions "
    "incl. the date. Serves the per-expiry OI tooltip stats of the "
    "OI-weighted moneyness skew panel."
)

OI_RESULT_COLUMNS = [
    "date", "option_type", "underlying_code", "expiry_date",
    "oi_total", "oi_delta_5d", "oi_delta_20d", "oi_max_20d",
]

OI_NUMERIC_COLS = [
    "oi_total", "oi_delta_5d", "oi_delta_20d", "oi_max_20d",
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

# ---- Options volatility index table ---------------------------------------
# Daily 30-day model-free implied-volatility index per underlying (CBOE VIX
# methodology adapted to exchange settlement prices; see
# docs/options_vol_smile_study.md). Date-granular (NOT expiry-granular) —
# no FK to options_expiry_identity.
VOL_INDEX_TABLE_NAME = "analysis.options_vol_index"
VOL_INDEX_ANALYSIS_NAME = "options_vol_index"

VOL_INDEX_PK_COLUMNS = ["underlying_code", "date"]

# Target constant maturity (calendar days) of the index.
VOL_INDEX_TARGET_DAYS = 30

VOL_INDEX_DESCRIPTION = (
    "Daily 30-day model-free implied-volatility index per underlying_code "
    "(CBOE VIX methodology adapted to exchange settlement prices, r=0.02, "
    "calendar-day T): per expiry sigma^2 = (2/T) * sum_i e^(rT) * "
    "(dK_i/K_i^2) * Q(K_i) - (1/T) * (F/K0 - 1)^2 over the OTM strip (puts "
    "below the forward, calls above, average of both at K0), F = S*exp(rT); "
    "the two expiries bracketing 30 days are time-interpolated to a "
    "constant-30-day variance, vol_index_30d = 100*sqrt(variance_30d) in "
    "vol points. PK (underlying_code, date); no FK (date-granular)."
)

VOL_INDEX_RESULT_COLUMNS = [
    "date", "underlying_code",
    "near_expiry_date", "far_expiry_date",
    "dte_near", "dte_far",
    "var_near", "var_far", "variance_30d", "vol_index_30d",
]

VOL_INDEX_NUMERIC_COLS = [
    "dte_near", "dte_far",
    "var_near", "var_far", "variance_30d", "vol_index_30d",
]

# ---- Options bands table (v2 — OI-MASS CORRIDOR) ---------------------------
# The options-bounded region per (underlying_code, date): strikes enclosing
# the central BANDS_CORRIDOR_MASS of pooled chain OI (all expiries,
# OI-CDF P10..P90), with spot's position in the mass (spot_pct), edge
# gaps, pos_state, a validated direction lean, and prior-session corridor
# events (EXIT_LOW/EXIT_HIGH/REENTER). Study 2026-10-04
# (temp_scripts/study_skew_position_bands.py, 3 CFFEX index underlyings
# 2020-2026): below the P25 edge (spot_pct < 0.25) mean 10-session
# +1.4% (n=341), with the deep P10 extreme at 90% up / mean +4.5%
# (n=10); >= P90 -> 89% down, mean -3.3% (n=9), while 75-90% is weak. The moneyness-skew level flags (all
# expiries' E[M] > 1) carry NO standalone edge (20% frequency, up-rate
# 0.484 vs 0.507 base) — stored as context; strongest combined state:
# skew_all_above & spot_pct <= 25% = mean 10-session +1.57% (n=289).
# Date-granular — no FK to options_expiry_identity.
BANDS_TABLE_NAME = "analysis.options_bands"
BANDS_ANALYSIS_NAME = "options_bands"

BANDS_PK_COLUMNS = ["underlying_code", "date"]

# Corridor = central mass share of pooled OI (CDF P25..P75 strikes).
# P25/P75 (not P10/P90): the quartile edges coincide with the validated
# lean thresholds (spot_pct <= 0.25 = UP zone) so corridor_low IS the
# lean boundary; the P10/P90 extremes stay the strongest-but-rarest
# snap-back levels (<= P10: 90% up, mean +4.5%, n=10).
BANDS_CORRIDOR_LO_QT = 0.25
BANDS_CORRIDOR_HI_QT = 0.75
# Lean thresholds on spot_pct (OI share at strikes <= spot): <=0.25 = UP
# (validated snap-back), >=0.90 = DOWN (only the top decile is reliably
# bearish; 75-90% is not).
BANDS_LEAN_LOW_PCT = 0.25
BANDS_LEAN_HIGH_PCT = 0.90
# Expected-move horizon (trading sessions) and annualization basis.
BANDS_EM_SESSIONS = 5
BANDS_EM_TRADING_DAYS = 252
# Plausibility floor for the source chain ATM IV (fractional; 10 vol
# points — A-share index ATM IV has never sustained below ~12 vol
# points). ETF-code underlyings' stats.options_greeks implied_vol is
# currently miscalibrated ~30x low (venue x1000 scaling — see
# fetch_vol_index_rows docstring; their max chain IV ~7.5 vol points);
# below this floor em5_pct/exhausted_5d are NULLed instead of emitting
# garbage. Revisit when the greeks calibration for SZSE/SSE ETF chains
# is fixed upstream.
BANDS_IV_MIN_FRACTION = 0.10

# Fresh strike-grid expansion window (sessions) and the amplifier
# threshold. Exchanges list new strikes as the underlying moves: a
# burst of new higher/lower strikes marks genuine range extension.
# Calibrated on the 2025-08-22..26 rally (000300 grid ceiling 4700 ->
# 5000 in three sessions; new_hi_40d 2 -> 6): the 2025-08-28 EXIT_HIGH
# breached into freshly-listed price territory -> amplified. Grids step
# by 2 strikes at a time, so >= 2 = one genuine expansion step.
BANDS_STRIKE_WIN_SESSIONS = 40
BANDS_SIG_MIN_NEW_STRIKES = 2

# Exit-quality re-study (2026-10-05,
# temp_scripts/study_corridor_events_v2.py, ~4,700 underlying-days): the
# stored event is a one-day binary cross of the PRIOR session's corridor
# edges — ~50% of exits breach by less than BANDS_EXIT_NOISE_Z x the
# day's own fluctuation (sigma20, trailing 20-session daily-return std)
# and CONTINUE in the breach direction (low side 29% up, mean 5-session
# -0.71%), while deep breaches (>= BANDS_EXIT_FLUSH_Z x sigma20) are the
# capitulation flush that snaps back (80% up, mean +2.0%; consistent per
# underlying: 000300 87.5%, 000852/000016 66.7%). Wall context: a
# low-side exit that also closes below the chain-dominant PUT wall's
# prior low while that wall was ACTIVE is the strongest observed cell
# (80% up, mean 5-session +3.12%).
BANDS_SIGMA_WIN_SESSIONS = 20   # trailing window of the daily-return std (fluctuation unit)
BANDS_EXIT_NOISE_Z = 0.5        # breach depth below this = NOISE (sub-fluctuation cross)
BANDS_EXIT_FLUSH_Z = 1.5        # breach depth at/above this = FLUSH (capitulation-grade)

BANDS_RESULT_COLUMNS = [
    "date", "underlying_code", "underlying_close",
    "corridor_low", "corridor_high", "oi_total", "spot_pct",
    "gap_low_pct", "gap_high_pct", "pos_state", "lean", "event",
    "sigma20", "dev_edge_pct", "dev_z", "exit_class",
    "wall_side_broken", "wall_state_prior",
    "new_hi_40d", "new_lo_40d", "edge_sig",
    "n_expiries", "skew_all_above", "skew_all_below", "min_em", "mean_em",
    "em5_pct", "ret5_pct", "exhausted_5d",
]

BANDS_NUMERIC_COLS = [
    "underlying_close",
    "corridor_low", "corridor_high", "oi_total", "spot_pct",
    "gap_low_pct", "gap_high_pct",
    "sigma20", "dev_edge_pct", "dev_z", "wall_side_broken",
    "new_hi_40d", "new_lo_40d",
    "n_expiries", "skew_all_above", "skew_all_below", "min_em", "mean_em",
    "em5_pct", "ret5_pct", "exhausted_5d",
]

BANDS_DESCRIPTION = (
    "Options-implied OI-MASS CORRIDOR bands per (underlying_code, date): the "
    "bounded region = strikes enclosing the central 80% of pooled chain OI "
    "(all expiries; corridor_low/corridor_high = OI-CDF P10/P90), with "
    "spot_pct (OI share at strikes <= spot), signed edge gaps, pos_state "
    "(BELOW/INSIDE/ABOVE), a validated direction lean (UP at/below the "
    "corridor bottom — close <= P10 = 90% up next 5 sessions, mean +4.5%; "
    "DOWN at/above the top — 89% down, mean -3.3%), and prior-session "
    "corridor events (EXIT_LOW/EXIT_HIGH/REENTER). Edge significance: "
    "new_hi_40d/new_lo_40d count strikes newly present beyond the chain's "
    "max/min strike of 40 sessions earlier (fresh exchange listing "
    "expansion), and edge_sig marks a breached/approached edge whose own "
    "side just expanded by >= 2 new strikes (UP/DOWN/BOTH; e.g. 000300 "
    "2025-08-28 EXIT_HIGH with new_hi_40d=6 -> UP — a breach into "
    "freshly-listed price territory). Exit quality (2026-10-05 re-study, "
    "~4,700 underlying-days): sigma20/dev_edge_pct/dev_z/exit_class grade "
    "every exit by breach depth in fluctuation units (NOISE < 0.5x sigma20 "
    "— ~half of all exits, continuation-leaning; BREAK 0.5..1.5; FLUSH "
    ">= 1.5 — the 80%-up snap-back cell), and wall_side_broken/"
    "wall_state_prior carry the chain-dominant wall context (an ACTIVE "
    "put wall's floor giving way on an EXIT_LOW = the strongest cell, "
    "80% up, mean 5-session +3.12%). Moneyness-skew flags "
    "(skew_all_above/below, min_em, mean_em) are context-only (no standalone "
    "edge; strongest combined state: all_above & spot_pct <= 25%, mean "
    "10-session +1.57%). em5_pct = 5-session chain-ATM IV expected move; "
    "exhausted_5d = |ret5| >= em5. PK (underlying_code, date); no FK "
    "(date-granular). Built by analysis.options."
)

# Band position states (pos_state CHECK)
BAND_POS_BELOW = "BELOW"
BAND_POS_INSIDE = "INSIDE"
BAND_POS_ABOVE = "ABOVE"

# Band direction lean (lean CHECK)
BAND_LEAN_UP = "UP"
BAND_LEAN_DOWN = "DOWN"
BAND_LEAN_NEUTRAL = "NEUTRAL"

# Band corridor events (event CHECK; vs the PRIOR session's corridor)
BAND_EVENT_NONE = "NONE"
BAND_EVENT_EXIT_LOW = "EXIT_LOW"
BAND_EVENT_EXIT_HIGH = "EXIT_HIGH"
BAND_EVENT_REENTER = "REENTER"

# Exit-quality classes (exit_class CHECK) by breach depth in fluctuation
# units (dev_z = dev_edge_pct / sigma20): NOISE = sub-fluctuation cross
# (dev_z < BANDS_EXIT_NOISE_Z) — continuation-leaning, not a level
# signal; BREAK = genuine but unresolved (between the thresholds);
# FLUSH = capitulation-grade breach (dev_z >= BANDS_EXIT_FLUSH_Z) — the
# validated snap-back cell.
BAND_EXIT_CLASS_FLUSH = "FLUSH"
BAND_EXIT_CLASS_BREAK = "BREAK"
BAND_EXIT_CLASS_NOISE = "NOISE"

# Edge-significance states (edge_sig CHECK): a breached/approached
# corridor edge whose OWN side just saw fresh strike-grid expansion
# (>= BANDS_SIG_MIN_NEW_STRIKES new strikes in the trailing
# BANDS_STRIKE_WIN_SESSIONS sessions). UP = upper edge (lean DOWN /
# EXIT_HIGH) + new HIGHER strikes; DOWN = lower edge (lean UP /
# EXIT_LOW) + new LOWER strikes; BOTH = both sides same day.
BAND_EDGE_SIG_NONE = "NONE"
BAND_EDGE_SIG_UP = "UP"
BAND_EDGE_SIG_DOWN = "DOWN"
BAND_EDGE_SIG_BOTH = "BOTH"

# Zone lifecycle states (analysis.options_walls.state CHECK)
WALL_STATE_ACTIVE = "ACTIVE"
WALL_STATE_ERODED = "ERODED"
WALL_STATE_BREACHED = "BREACHED"

# ---- Options moneyness series tables ---------------------------------------
# Materialized read models behind /api/szse-options/oi-moneyness-series
# (the OI-weighted Moneyness Skew panel). options_moneyness_series holds
# the per (underlying_code, date, expiry_month) oi_moneyness aggregates;
# options_spot_daily the per (underlying_code, date) underlying close
# (the panel's spot axis, incl. dates with no valid group). Both replace
# a former live 5-way full-history join per API request (~4.8s cold on
# the biggest CFFEX underlying). No FK (month-group / date-granular keys).
MONEYNESS_TABLE_NAME = "analysis.options_moneyness_series"
MONEYNESS_ANALYSIS_NAME = "options_moneyness_series"

MONEYNESS_PK_COLUMNS = ["underlying_code", "date", "expiry_month"]

MONEYNESS_RESULT_COLUMNS = [
    "date", "underlying_code", "expiry_month", "expiry_date",
    "n_valid", "e_m", "wsum_k", "wsum",
    "call_oi", "put_oi", "call_otm_oi", "put_otm_oi",
]

MONEYNESS_NUMERIC_COLS = [
    "e_m", "wsum_k", "wsum",
    "call_oi", "put_oi", "call_otm_oi", "put_otm_oi",
]

# Per-group e_m gate (browser/server parity): fewer valid rows -> e_m NULL.
MONEYNESS_MIN_VALID = 3

MONEYNESS_DESCRIPTION = (
    "Materialized oi_moneyness skew series per (underlying_code, date, "
    "expiry_month): per month group and date — n_valid (valid rows: "
    "expiry_date >= date AND 0 < implied_vol < 5), wsum_k/wsum "
    "(GREATEST(OI,1)-weighted strike and weight sums), e_m = (Σ w·K / Σ w) "
    "/ day close (NULL when n_valid < 3 or close missing/<= 0), call/put "
    "OI and OTM OI sums over the group's active rows, expiry_date = MIN "
    "valid expiry. The precomputed read model behind the OI-weighted "
    "Moneyness Skew panel's /oi-moneyness-series endpoint (replaces the "
    "former live 5-way full-history join, ~4.8s cold on the biggest CFFEX "
    "underlying). PK (underlying_code, date, expiry_month); no FK "
    "(month-group keys)."
)

SPOT_TABLE_NAME = "analysis.options_spot_daily"
SPOT_ANALYSIS_NAME = "options_spot_daily"

SPOT_PK_COLUMNS = ["underlying_code", "date"]

SPOT_RESULT_COLUMNS = ["date", "underlying_code", "close"]

SPOT_NUMERIC_COLS = ["close"]

SPOT_DESCRIPTION = (
    "Per (underlying_code, date) underlying close from the options loader "
    "(MAX(options_settlement.underlying_close) over the date's settlement "
    "rows; close <= 0 is mapped to null by the API). Serves the spot axis "
    "of /oi-moneyness-series (dates with no valid moneyness group "
    "included). PK (underlying_code, date); no FK (date-granular)."
)

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
