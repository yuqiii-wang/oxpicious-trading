"""Configuration constants for the ``dividends`` submodule of
analyze.pe_and_dividends: the daily analysis.dividends table identity
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
