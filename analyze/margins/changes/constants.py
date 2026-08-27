"""Constants for margin_changes: tunable params, column lists, description.

All shared constants live here so that detection / trading_amt / db_io /
runner can import from a single place.
"""
from analyze.margins.config import TABLE_CHANGES


# ---- Tunable parameters --------------------------------------------------

# Minimum trading days for a trend to be recorded. Trends with <= 2 days
# are considered noise and are dropped. The user spec says "> 2 days_of_trend".
MIN_TREND_DAYS = 3

# Maximum length of an opposite-direction "gap" segment to be bridged
# (absorbed) into the surrounding same-direction segments. If a short
# counter-trend run of <= BRIDGE_GAP_DAYS occurs between two same-direction
# runs, it is flipped to match, merging the three runs into one longer
# trend. This prevents single-day noise from fragmenting meaningful trends.
BRIDGE_GAP_DAYS = 3

# Minimum fraction of trend days that must have a STATISTICALLY
# SIGNIFICANT slope (|zscore_20d| > 0) for the trend to be kept.
# 0.5 = majority. Lower = more permissive; 1.0 = ALL days.
ZSCORE_MAJORITY_THRESHOLD = 0.5


# ---- DB column lists -----------------------------------------------------

# Column order for COPY-insert into margin_changes (matches the table
# schema).
INSERT_COLUMNS = [
    "code", "sec_type",
    "start_date", "end_date", "days_of_trend",
    "is_trend_up_not_down",
    "new_buy", "rz_buy_vs_trading_amt_ratio",
]

# Numeric columns that need rounding / NaN→NULL sanitization before INSERT.
NUMERIC_COLS = [
    "new_buy", "rz_buy_vs_trading_amt_ratio",
]


# ---- analysis_identity description ---------------------------------------

DESCRIPTION = (
    "Per-(sec_type, code, trend) summary of SIGNIFICANT margin balance "
    "TRENDS (sustained UP or DOWN moves) on the RONGZI (融资 / cash-borrow) "
    "margin balance curve. One row per trend episode: [start_date, "
    "end_date] span with direction (is_trend_up_not_down), span length "
    "(days_of_trend > 2), new_buy (rz_buy on the episode end_date — the "
    "last day's fresh rongzi buy amount), and rz_buy_vs_trading_amt_ratio "
    "(Σ rz_buy / Σ trading_amount — fraction of turnover from rongzi "
    "buys). sec_type ∈ {etf, stock, index} — ''index'' rows aggregated "
    "from margin_index_series TABLE. RONQIN (融券 / sec borrow) EXCLUDED. "
    "Trend detection: contiguous run of same-sign 5-day smoothed balance "
    "slope (margin_balance_slope_ma5 > 0 = UP, < 0 = DOWN), min 3 days. "
    "DIRECTION from slope_ma5 sign (actual balance movement). GAP "
    "BRIDGING: short opposite-direction runs of <= 3 days between two "
    "same-direction runs are absorbed (flipped to match), preventing "
    "single-day noise from fragmenting meaningful trends. SIGNIFICANCE "
    "FILTER: MAJORITY (>50%) of trend days must have |zscore_20d| > 0 "
    "(statistically significant slope vs 20d history). Built by "
    "analyze.margins (truncate-then-recompute); all INSERTs in Python per "
    "project rule."
)
