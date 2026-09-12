"""PE & Dividend Yield analysis (ETF + Index + Stock).

Populates analysis.pe + analysis.dividends (2026-09 split of the former
combined analysis.pe_and_dividends table; one builder pipeline writes
both):
analysis.pe_and_dividend_stats (monthly 5y rolling stats),
analysis.pe_and_dividend_pct (monthly trailing percentile bands of
pe / dividend_yield) and analysis.pe_and_dividend_pct_streaks
(band-break excursion streaks audited against those bands — the
mov_ave_high_low_pct[_streaks] pattern applied to the valuation
metrics).

Run via ``python -m analyze.pe_and_dividends``.
"""
