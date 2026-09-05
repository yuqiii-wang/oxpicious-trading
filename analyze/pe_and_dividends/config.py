"""Configuration constants for analyze.pe_and_dividends."""
ANALYSIS_NAME = "pe_and_dividends"
DETAIL_TABLE = "analysis.pe_and_dividends"
STATS_TABLE = "analysis.pe_and_dividend_stats"

DESCRIPTION = (
    "PE & Dividend Yield analysis (ETF + Index + Stock). Per-(sec_type, "
    "code, date) pe_ma20 (20-day MA of PE, index-only) and dividend_yield "
    "(trailing-12m D/P, fractional ratio). Close and raw PE are NOT stored "
    "(live in stats). Index dividend_yield = cap-weighted average of "
    "constituent trailing-12m yields (SUM w_s x dps_s/close_s) using the "
    "LATEST sec_composition snapshot (temporal extrapolation) — NOT "
    "weighted-DPS / index-close, which mixes per-share CNY with index "
    "points and understates the yield ~100x. Monthly 5y rolling stats "
    "(min/max PE, min/max div, dividend_var, dividend_stability) in "
    "pe_and_dividend_stats."
)

SEC_TYPES = ("index", "etf", "stock")

SEC_TYPE_IDENTITY_TABLE = {
    "etf":   "stats.etf_identity",
    "index": "stats.index_identity",
    "stock": "stats.stock_identity",
}

# 20-trading-day MA window for pe_ma20.
PE_MA_WINDOW = 20

# Trailing-12m dividend window in calendar days.
TRAILING_DIVIDEND_DAYS = 365

# Rolling 5-year window in trading days (~255 × 5).
ROLLING_5Y_DAYS = 1275

# Calendar years for dividend_stability_5y.
STABILITY_WINDOW_YEARS = 5


# ============================================================================
#  analysis.pe_and_dividend_pct — metric percentile BANDS
#
#  Monthly trailing percentile bands of the pe_ma20 / dividend_yield series
#  (the analysis.mov_ave_high_low_pct pattern applied to the two valuation
#  metrics; see pct_bands.py). Both band legs are percentiles of the SAME
#  series — a valuation metric has one value per day, so there are no
#  separate high/low legs.
# ============================================================================

PD_PCT_TABLE = "analysis.pe_and_dividend_pct"
PD_PCT_NAME = "pe_and_dividend_pct"

# Audited metric series (the `metric` PK column): the two value columns of
# analysis.pe_and_dividends. Each metric's history is banded independently
# (its NULL patterns differ — pe_ma20 needs a PE source; dividend_yield is
# NULL until the first trailing-12m dividend window fills).
PD_PCT_METRICS = ("pe_ma20", "dividend_yield")

# Lookback window lengths in metric observations (the `period` PK column):
# 255 / 500 / 750 / 1275 = ~1 / 2 / 3 / 5 trading years (the ma255
# yearly-window precedent, shared with mov_ave_spread's HIGH_LOW_PCT_*).
# The window is TRAILING (backward-only), ending inclusive at the month's
# last non-NULL observation row of the metric.
PD_PCT_PERIODS = (255, 500, 750, 1275)

# Band tightness levels (percent). The band is SYMMETRIC on the metric's
# own distribution: low_val = pct_type-th percentile, high_val =
# (100 - pct_type)-th percentile of the window's values. 1 = near-full
# range ([1st, 99th]); 10 = core envelope ([10th, 90th]).
PD_PCT_TYPES = (1, 5, 10)

# Minimum non-NULL observations for a band: 255 rows (1 trading year, the
# HIGH_LOW_PCT_MIN_PERIODS precedent), shared by all periods. Fewer yields
# no band (the month is skipped — high_val/low_val are NOT NULL).
PD_PCT_MIN_PERIODS = 255

# Rows per (sec_type, code, date_year_month, metric) triple when complete:
# one band per (period, pct_type). The missing-triple detection counts rows
# against this (crash-consistency guard against partially-inserted triples).
PD_PCT_ROWS_PER_TRIPLE = len(PD_PCT_PERIODS) * len(PD_PCT_TYPES)

# All output column names of analysis.pe_and_dividend_pct in the order they
# appear in the table (must stay in sync with the CREATE TABLE in
# 11_pe_and_dividends.sql — COPY inserts with this explicit column order).
PD_PCT_COLUMNS = (
    "sec_type", "code", "date_year_month", "metric", "period", "pct_type",
    "high_val", "low_val",
)

PD_PCT_DESCRIPTION = (
    "Per-(sec_type, code, month, metric, period, pct_type) percentile "
    "BAND of the pe_ma20 / dividend_yield series from "
    "analysis.pe_and_dividends (the mov_ave_high_low_pct pattern applied "
    "to the valuation metrics). low_val = pct_type-th and high_val = "
    "(100 - pct_type)-th percentile (linear interpolation) of the "
    "metric's non-NULL values over the TRAILING window of `period` "
    "observations (255/500/750/1275 = ~1/2/3/5 trading years) ending at "
    "the month's last observation row; both legs use the SAME series. One "
    "band per calendar month stored under the month's first day; the "
    "in-progress month is skipped (no true month-end anchor yet); fewer "
    "than 255 non-NULL observations yields no band. Trailing windows make "
    "completed months immutable — only missing (code, month, metric) "
    "triples are computed incrementally (a triple is complete when all 12 "
    "period x pct_type rows exist); --force / single-code rebuilds the "
    "scope. Internal step of analyze.pe_and_dividends (pct_bands.py)."
)


# ============================================================================
#  analysis.pe_and_dividend_pct_streaks — band-break excursion streaks
#
#  One row per excursion streak per (sec_type, code, metric, period,
#  pct_type): maximal consolidations of same-side days whose metric value
#  falls ABOVE the band's high_val or BELOW low_val, tolerating in-band
#  re-entries of up to PD_PCT_GAP_TOLERANCE consecutive trading days.
#  Internal step of the parent pe_and_dividends pipeline (see
#  pct_streaks.py) — joins the in-memory detail frame against the bands
#  table computed earlier in the same run.
# ============================================================================

PD_PCT_STREAKS_TABLE = "analysis.pe_and_dividend_pct_streaks"
PD_PCT_STREAKS_NAME = "pe_and_dividend_pct_streaks"

# In-band gap tolerance in consecutive trading days (the
# HIGH_LOW_PCT_GAP_TOLERANCE semantics): a re-entry into the band of up to
# GAP_TOLERANCE days does NOT break an excursion streak (the gap is
# bridged — those days count in day_count); a longer gap ends the streak.
# A side switch (above -> below or vice versa) always ends the streak.
PD_PCT_GAP_TOLERANCE = 5

# All output column names of analysis.pe_and_dividend_pct_streaks in the
# order they appear in the table (must stay in sync with the CREATE TABLE
# in 11_pe_and_dividends.sql — COPY inserts with this explicit column
# order). PK is (sec_type, code, date_year_month, metric, period,
# pct_type, start_date, end_date).
PD_PCT_STREAKS_COLUMNS = (
    "sec_type", "code", "date_year_month", "metric", "period", "pct_type",
    "start_date", "end_date", "start_value", "end_value", "max_value",
    "min_value", "day_count", "std_dev",
)

PD_PCT_STREAKS_DESCRIPTION = (
    "Band-BREAK excursion streaks audited against "
    "analysis.pe_and_dividend_pct (the mov_ave_high_low_pct_streaks "
    "pattern applied to pe_ma20 / dividend_yield). A day is OUT-OF-BAND "
    "when its metric value is ABOVE its own month-band high_val or BELOW "
    "low_val (value-based breakout test). An excursion streak is the "
    "maximal consolidation of same-side out-of-band TRADING days where "
    "re-entries of up to 5 consecutive trading days are TOLERATED "
    "(bridged — the in-band gap stays inside the streak's span); a "
    "longer in-band gap ends the streak, as does a side switch (above -> "
    "below or vice versa starts a new streak). start_date/end_date bound "
    "the span (first/last OUT-OF-BAND day; bridged in-band days in "
    "between count in day_count). Non-trading vendor rows (ffilled "
    "weekday holidays) and NULL-metric rows are excluded before "
    "classification — spans, gap tolerance and day_count are in REAL "
    "observation days. Streaks can span calendar months (each day is "
    "tested against its OWN month's band); date_year_month records the "
    "START month. Episodes SHIFT with new data (the last streak of a "
    "code is open-ended until a 6+-day in-band gap or side switch closes "
    "it, and trailing in-band days may become a bridged gap later), so "
    "streaks are rebuilt WHOLESALE per sec_type (per code in single-code "
    "mode) on every run that processes the scope. The side (high/low) is "
    "NOT stored — the API derives it from the END month's band (a streak "
    "never switches sides). Internal step of analyze.pe_and_dividends "
    "(pct_streaks.py)."
)
