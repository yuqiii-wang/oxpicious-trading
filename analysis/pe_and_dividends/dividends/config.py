"""Configuration constants for the ``dividends`` submodule of
analysis.pe_and_dividends: the daily analysis.dividends table identity
and the trailing-12m dividend window. (The former annual
analysis.dividends_stats snapshot and the dividends_pct[_streaks]
bands/streaks half were removed 2026-09-28 with their only consumer,
the UI's PE-only Valuation Streaks card.)
"""
ANALYSIS_NAME_DIVIDENDS = "dividends"
DIVIDENDS_TABLE = "analysis.dividends"

DESCRIPTION_DIVIDENDS = (
    "Dividend yield (ETF + Index + Stock). Per-(sec_type, code, date) "
    "trailing-12m D/P, fractional ratio; rows only where the yield is "
    "defined (strictly positive — non-payers have no row). Index "
    "dividend_yield = cap-weighted average of constituent trailing-12m "
    "yields (SUM w_s x dps_s/close_s) using the LATEST sec_composition "
    "snapshot (temporal extrapolation) — NOT weighted-DPS / index-close, "
    "which mixes per-share CNY with index points and understates the "
    "yield ~100x. Split (2026-09) from the former combined "
    "analysis.pe_and_dividends table."
)

# Trailing-12m dividend window in calendar days.
TRAILING_DIVIDEND_DAYS = 365

# ---------------------------------------------------------------------------
#  Dividend price estimation curve ("tech" stage, 2026-10-03; ETF only).
#  Renamed dividend_tech_stats -> dividend_est 2026-10-03.
# ---------------------------------------------------------------------------
ANALYSIS_NAME_TECH = "dividend_est"
TECH_TABLE = "analysis.dividend_est"

DESCRIPTION_TECH = (
    "Dividend price estimation curve (ETF only for now). Per-(sec_type, "
    "code, date) cum-dividend fair-value line: anchored on the post-drop "
    "close of each ex-dividend date, growing exponentially to the next "
    "cum-dividend level (close(next ex) + next D) by the next ex-date — "
    "the ex-dividend sawtooth pricing scheme (record-date full value, "
    "next-day minus dividend, exponential regrowth). Open tail after the "
    "last ex-date grows at ln(1 + D_last/close(anchor))/freq_days where "
    "freq_days = median of the last up-to-3 inter-ex-date gaps (365 when "
    "only one event). div_est_price is in RAW price units; the API "
    "converts to adjusted space for the chart overlay. Tail rows are "
    "mutable (recomputed when the next ex-date lands) — incremental runs "
    "re-upsert from the stored tail's seg_start_date onward plus missing "
    "dates."
)

# Fallback dividend frequency (calendar days) for codes with a single
# ex-dividend event (no inter-event gap to median).
TECH_FALLBACK_FREQ_DAYS = 365.0

# How many trailing inter-ex-date gaps form the dividend frequency estimate.
TECH_FREQ_GAP_COUNT = 3

# ---------------------------------------------------------------------------
#  Dividend delivery audit ("deliveries" stage, 2026-10-03; ETF + stock)
# ---------------------------------------------------------------------------
ANALYSIS_NAME_DELIVERIES = "dividend_deliveries"
DELIVERIES_TABLE = "analysis.dividend_deliveries"

DESCRIPTION_DELIVERIES = (
    "Rolling 5-year (1-year stride) dividend delivery audit per (sec_type, "
    "code; etf + stock — an index has no per-share deliveries). One row "
    "per annual window anchor (each calendar year's last data date; the "
    "current year anchors at the code's latest close — the open, mutable "
    "window), aggregating the delivery events inside the window: "
    "deliveries count, years with a delivery, avg per-event yield "
    "(D/close at the ex-date), annualized yield (sum of per-event "
    "yields / window span years), latest-year yield (sum of per-event "
    "yields over the final 365d of the window — the trailing-1y yield "
    "at the anchor), avg per-event eps (close/pe) and the "
    "stability share — an event is STABLE when the same code also "
    "delivered within +/- 20 days of the same date one year earlier "
    "(365d +/- 20d repeat window; leap-year drift of +/- 1d sits inside "
    "the tolerance). Rows only for payer codes and only from the first "
    "delivery onward; zero-delivery windows are stored with 0 counts and "
    "NULL aggregates. Tiny table — rebuilt wholesale per sec_type on "
    "every run that processes the stage (the pe_pct_streaks precedent)."
)

# Rolling audit window length in calendar years (stride is 1 year).
DELIVERIES_WINDOW_YEARS = 5

# Stability tolerance: an event is STABLE when the same code also
# delivered within +/- this many days of the same date one year earlier
# (365-day center; leap-year drift of +/- 1d sits inside the tolerance).
DELIVERIES_STABILITY_TOL_DAYS = 20
