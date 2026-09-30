"""Configuration constants for the ``pe`` submodule of
analyze.pe_and_dividends: the daily analysis.pe table identity and the
analysis.pe_pct / analysis.pe_pct_streaks bands-and-streaks settings
(2026-09-28: PE-only — the former two-metric pe_pct / dividends_pct
split lost its dividend_yield half with the PE-only Valuation Streaks
card).
"""
ANALYSIS_NAME_PE = "pe"
PE_TABLE = "analysis.pe"

DESCRIPTION_PE = (
    "PE (ETF + Index + Stock). Per-(sec_type, code, date) raw PE — "
    "index PE from stats.index_valuation.pe, etf/stock PE pre-computed "
    "by builds.etf / builds.stock; rows only for source days with a "
    "valid (strictly positive) PE. Split (2026-09) from the former "
    "combined analysis.pe_and_dividends table."
)


# ============================================================================
#  analysis.pe_pct — PE percentile BANDS
#
#  Monthly trailing percentile bands of the pe series (the
#  analysis.mov_ave_high_low_pct pattern applied to the valuation metric;
#  see pct_bands.py). Both band legs are percentiles of the SAME series —
#  a valuation metric has one value per day, so there are no separate
#  high/low legs.
# ============================================================================

# Band table (the audited series: the pe column of analysis.pe).
PE_PCT_TABLE = "analysis.pe_pct"
PE_PCT_NAME = "pe_pct"

# Lookback window lengths in pe observations (the `period` PK column):
# 255 / 500 / 750 / 1275 = ~1 / 2 / 3 / 5 trading years (the ma255
# yearly-window precedent, shared with mov_ave_spread's HIGH_LOW_PCT_*).
# The window is TRAILING (backward-only), ending inclusive at the month's
# last non-NULL observation row of the metric.
PE_PCT_PERIODS = (255, 500, 750, 1275)

# Band tightness levels (percent). The band is SYMMETRIC on the pe
# distribution: low_val = pct_type-th percentile, high_val =
# (100 - pct_type)-th percentile of the window's values. 1 = near-full
# range ([1st, 99th]); 10 = core envelope ([10th, 90th]).
PE_PCT_TYPES = (1, 5, 10)

# Minimum non-NULL observations for a band: 255 rows (1 trading year, the
# HIGH_LOW_PCT_MIN_PERIODS precedent). Fewer yields no band (the month is
# skipped — high_val/low_val are NOT NULL).
PE_PCT_MIN_PERIODS = 255

# Rows per (sec_type, code, date_year_month) triple when complete: one
# band per (period, pct_type). The missing-triple detection counts rows
# against this (crash-consistency guard against partially-inserted
# triples).
PE_PCT_ROWS_PER_TRIPLE = len(PE_PCT_PERIODS) * len(PE_PCT_TYPES)

# All output column names of analysis.pe_pct in the order they appear in
# the table (must stay in sync with the CREATE TABLE in
# 11_pe_and_dividends.sql — COPY inserts with this explicit column
# order).
PE_PCT_COLUMNS = (
    "sec_type", "code", "date_year_month", "period", "pct_type",
    "high_val", "low_val",
)

PE_PCT_DESCRIPTION = (
    "Per-(sec_type, code, month, period, pct_type) percentile BAND of "
    "the pe series from analysis.pe (the mov_ave_high_low_pct pattern "
    "applied to the valuation metric). low_val = pct_type-th and "
    "high_val = (100 - pct_type)-th percentile (linear interpolation) "
    "of the pe series' non-NULL values over the TRAILING window of "
    "`period` observations (255/500/750/1275 = ~1/2/3/5 trading years) "
    "ending at the month's last observation row; both legs use the SAME "
    "series. One band per calendar month stored under the month's first "
    "day; the in-progress month is skipped (no true month-end anchor "
    "yet); fewer than 255 non-NULL observations yields no band. Trailing "
    "windows make completed months immutable — only missing (code, "
    "month) triples are computed incrementally (a triple is complete "
    "when all 12 period x pct_type rows exist); --force / single-code "
    "rebuilds the scope. Internal step of analyze.pe_and_dividends "
    "(pe/pct_bands.py)."
)


# ============================================================================
#  analysis.pe_pct_streaks — PE band-break excursion streaks
#
#  One row per excursion streak per (sec_type, code, period, pct_type):
#  maximal consolidations of same-side days whose pe value falls ABOVE
#  the band's high_val or BELOW low_val, tolerating in-band re-entries of
#  up to PE_PCT_GAP_TOLERANCE consecutive trading days. Internal step of
#  the parent pe_and_dividends pipeline (see pct_streaks.py) — joins the
#  in-memory detail frame against the pe_pct bands computed earlier in
#  the same run.
# ============================================================================

PE_PCT_STREAKS_TABLE = "analysis.pe_pct_streaks"
PE_PCT_STREAKS_NAME = "pe_pct_streaks"

# In-band gap tolerance in consecutive trading days (the
# HIGH_LOW_PCT_GAP_TOLERANCE semantics): a re-entry into the band of up to
# GAP_TOLERANCE days does NOT break an excursion streak (the gap is
# bridged — those days count in day_count); a longer gap ends the streak.
# A side switch (above -> below or vice versa) always ends the streak.
PE_PCT_GAP_TOLERANCE = 5

# All output column names of analysis.pe_pct_streaks in the order they
# appear in the table (must stay in sync with the CREATE TABLE in
# 11_pe_and_dividends.sql — COPY inserts with this explicit column
# order). PK is (sec_type, code, date_year_month, period, pct_type,
# start_date, end_date).
PE_PCT_STREAKS_COLUMNS = (
    "sec_type", "code", "date_year_month", "period", "pct_type",
    "start_date", "end_date", "start_value", "end_value", "max_value",
    "min_value", "day_count", "std_dev",
)

PE_PCT_STREAKS_DESCRIPTION = (
    "Band-BREAK excursion streaks of the pe series audited against "
    "analysis.pe_pct (the mov_ave_high_low_pct_streaks pattern applied "
    "to the valuation metric). A day is OUT-OF-BAND when its pe value "
    "is ABOVE its own month-band high_val or BELOW low_val (value-based "
    "breakout test). An excursion streak is the maximal consolidation of "
    "same-side out-of-band TRADING days where re-entries of up to 5 "
    "consecutive trading days are TOLERATED (bridged — the in-band gap "
    "stays inside the streak's span); a longer in-band gap ends the "
    "streak, as does a side switch (above -> below or vice versa starts "
    "a new streak). start_date/end_date bound the span (first/last "
    "OUT-OF-BAND day; bridged in-band days in between count in "
    "day_count). Non-trading vendor rows (ffilled weekday holidays) and "
    "NULL-pe rows are excluded before classification — spans, gap "
    "tolerance and day_count are in REAL observation days. Streaks can "
    "span calendar months (each day is tested against its OWN month's "
    "band); date_year_month records the START month. Episodes SHIFT "
    "with new data (the last streak of a code is open-ended until a "
    "6+-day in-band gap or side switch closes it, and trailing in-band "
    "days may become a bridged gap later), so streaks are rebuilt "
    "WHOLESALE per sec_type (per code in single-code mode) on every run "
    "that processes the scope. The side (high/low) is NOT stored — the "
    "API derives it from the END month's band (a streak never switches "
    "sides). Internal step of analyze.pe_and_dividends "
    "(pe/pct_streaks.py)."
)
