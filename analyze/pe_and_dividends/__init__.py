"""PE & Dividend Yield analysis (ETF + Index + Stock).

Populates analysis.pe_and_dividends (daily pe_ma20 + dividend_yield),
analysis.pe_and_dividend_stats (monthly 5y rolling stats),
analysis.pe_and_dividend_pct (monthly trailing percentile bands of
pe_ma20 / dividend_yield) and analysis.pe_and_dividend_pct_streaks
(band-break excursion streaks audited against those bands — the
mov_ave_high_low_pct[_streaks] pattern applied to the valuation
metrics).

Run via ``python -m analyze.pe_and_dividends``.
"""
