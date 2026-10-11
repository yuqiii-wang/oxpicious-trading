"""High/low percentile band + excursion streak constants for
analysis.mov_ave_spread.

analysis.mov_ave_high_low_pct — multi-period high/low percentile bands
(one row per sec_type, code, date_year_month, period, pct_type: a
symmetric quantile envelope of the security's daily price range over the
lookback period) and analysis.mov_ave_high_low_pct_streaks — the
band-break excursion streaks audited against those bands. Internal steps
of the parent mov_ave_spread pipeline (see steps/high_low_pct.py and
steps/high_low_pct_streaks.py).
"""
HIGH_LOW_PCT_TABLE = "analysis.mov_ave_high_low_pct"
HIGH_LOW_PCT_ANALYSIS_NAME = "mov_ave_high_low_pct"

# Lookback window lengths in trading rows (the `period` PK column):
# 60 / 120 / 255 / 500 / 750 / 1275 = ~0.25 / 0.5 / 1 / 2 / 3 / 5 trading
# years (the ma255 yearly-window precedent). The window is TRAILING
# (backward-only), ending inclusive at the month's last trading row.
HIGH_LOW_PCT_PERIODS = (60, 120, 255, 500, 750, 1275)

# Band tightness levels (percent). The LOW leg uses the pct_type-th
# percentile of daily low prices; the HIGH leg the (100 - pct_type)-th
# percentile of daily high prices — a SYMMETRIC band: 1 = near-full
# range of the window ([1st pct of lows, 99th pct of highs]), 10 = core
# envelope ([10th, 90th]). Mirrors the pct_type column (part of the PK)
# in database/sql/analysis/mov_ave_spreads/07_mov_ave_high_low_pct.sql.
HIGH_LOW_PCT_TYPES = (1, 5, 10)

# Minimum observations for a band: 255 rows (1 trading year) shared by
# all periods >= 255; shorter windows (60 / 120) use their own window
# length (pandas requires min_periods <= window). Windows near a code's
# history start are naturally truncated; fewer than 255 rows yields no
# pair EXPECTATION at detection time (the month is skipped —
# high_val/low_val are NOT NULL).
HIGH_LOW_PCT_MIN_PERIODS = 255

# Rows per (sec_type, code, date_year_month) pair when complete: one
# band per (period, pct_type). The missing-pair detection counts rows
# against this (a pair with any rows absent is re-computed —
# crash-consistency guard against partially-inserted pairs).
HIGH_LOW_PCT_ROWS_PER_PAIR = len(HIGH_LOW_PCT_PERIODS) * len(HIGH_LOW_PCT_TYPES)

# All output column names of analysis.mov_ave_high_low_pct in the order
# they appear in the table (must stay in sync with the CREATE TABLE in
# mov_ave_spreads/07_mov_ave_high_low_pct.sql — COPY inserts with this
# explicit column
# order). PK is (sec_type, code, date_year_month, period, pct_type).
HIGH_LOW_PCT_COLUMNS = (
    "sec_type", "code", "date_year_month", "period", "pct_type",
    "high_val", "low_val",
)

HIGH_LOW_PCT_DESCRIPTION = (
    "Multi-period high/low price percentile BAND (ETF + Index + "
    "Stock). One row per (sec_type, code, date_year_month, period, "
    "pct_type): a symmetric quantile envelope of the security's daily "
    "price range over the lookback period — low_val = the pct_type-th "
    "percentile of daily LOW prices, high_val = the (100 - pct_type)-"
    "th percentile of daily HIGH prices (linear interpolation), both "
    "over the TRAILING (backward-only) window of `period` trading "
    "rows (60/120/255/500/750/1275 = ~0.25/0.5/1/2/3/5 trading years "
    "— the ma255 yearly-window precedent) ENDING at date_year_month's "
    "last trading row. pct_type 1 = near-full range of the window "
    "([1st pct of lows, 99th pct of highs]); pct_type 10 = core "
    "envelope ([10th, 90th]). One band per calendar month, anchored at "
    "the month's last trading row and stored under the month's first "
    "day — 18 bands (6 periods x 3 pct_types) per (sec_type, code, "
    "month). Windows near a code's history start are naturally "
    "truncated (a period needs only its own window length of "
    "observations); pair detection expects a month only once 255 rows "
    "(1 trading year) of history exist. Trailing windows make "
    "historical bands immutable once computed — only missing "
    "(code, month) pairs are computed incrementally (a pair is "
    "complete when all 18 rows exist); --force rebuilds the whole "
    "scope. Internal step of analysis.mov_ave_spread "
    "(steps/high_low_pct.py), reusing the parent source DataFrame's "
    "high/low columns (no second DB round-trip). The sec_type column "
    "discriminates the source universe ('etf' | 'index' | 'stock')."
)


# ---- Band-break excursion streaks ------------------------------------------

HIGH_LOW_PCT_STREAKS_TABLE = "analysis.mov_ave_high_low_pct_streaks"
HIGH_LOW_PCT_STREAKS_NAME = "mov_ave_high_low_pct_streaks"

# In-band gap tolerance in consecutive trading days: a re-entry into
# the band of up to GAP_TOLERANCE days does NOT break an excursion
# streak (the gap is bridged — those days count in day_count); a gap
# of GAP_TOLERANCE + 1 days ends the streak. A side switch (above ->
# below or vice versa) always ends the streak regardless of gap size.
HIGH_LOW_PCT_GAP_TOLERANCE = 5

# All output column names of analysis.mov_ave_high_low_pct_streaks in
# the order they appear in the table (must stay in sync with the
# CREATE TABLE in mov_ave_spreads/08_mov_ave_high_low_pct_streaks.sql —
# COPY inserts with this
# explicit column order). PK is (sec_type, code, date_year_month,
# period, pct_type, start_date, end_date).
HIGH_LOW_PCT_STREAKS_COLUMNS = (
    "sec_type", "code", "date_year_month", "period", "pct_type",
    "start_date", "end_date", "open", "close", "high", "low",
    "day_count", "std_dev", "daily_ave_trading_amt",
)

HIGH_LOW_PCT_STREAKS_DESCRIPTION = (
    "Band-BREAK excursion streaks audited against "
    "analysis.mov_ave_high_low_pct (ETF + Index + Stock). One row per "
    "excursion streak per (sec_type, code, period, pct_type): a day is "
    "OUT-OF-BAND when its adjusted close falls ABOVE the band's "
    "high_val or BELOW low_val (close-based breakout test — intraday "
    "spikes do not trigger; the streak's own high/low columns carry "
    "that information). An excursion streak is the maximal "
    "consolidation of same-side out-of-band days where re-entries into "
    "the band of up to 5 consecutive trading days are TOLERATED "
    "(bridged — the in-band gap stays inside the streak's span); a "
    "longer in-band gap ends the streak, as does a side switch (above "
    "-> below or vice versa starts a new streak). start_date/end_date "
    "bound the span (first/last OUT-OF-BAND trading row; bridged "
    "in-band days in between count in day_count). Streaks can span "
    "calendar months; date_year_month records the START month. "
    "Episodes SHIFT with new data (the last streak of a code is "
    "open-ended until a 6+-day in-band gap or side switch closes it, "
    "and trailing in-band days may become a bridged gap later), so "
    "streaks are rebuilt WHOLESALE per sec_type on every run that "
    "processes the sec_type (the daily-registries /"
    "margin_changes precedent). Internal step of "
    "analysis.mov_ave_spread (steps/high_low_pct_streaks.py), joining "
    "the parent source DataFrame against the bands table computed "
    "earlier in the same run."
)
