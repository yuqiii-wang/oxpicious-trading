"""Configuration constants for analyze.pe_and_dividends."""
ANALYSIS_NAME = "pe_and_dividends"
DETAIL_TABLE = "analysis.pe_and_dividends"
STATS_TABLE = "analysis.pe_and_dividend_stats"

DESCRIPTION = (
    "PE & Dividend Yield analysis (ETF + Index + Stock). Per-(sec_type, "
    "code, date) pe_ma20 (20-day MA of PE, index-only) and dividend_yield "
    "(trailing-12m D/P, fractional ratio). Close and raw PE are NOT stored "
    "(live in stats). Index dividend_yield aggregates constituent stock "
    "dividends weighted by the LATEST sec_composition snapshot (temporal "
    "extrapolation). Monthly 5y rolling stats (min/max PE, min/max div, "
    "dividend_var, dividend_stability) in pe_and_dividend_stats."
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
