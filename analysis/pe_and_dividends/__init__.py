"""PE & Dividend Yield analysis (ETF + Index + Stock).

Populates the per-metric split table families (the 2026-09 splits of
the former combined analysis.pe_and_dividends /
pe_and_dividend_pct / pe_and_dividend_pct_streaks
tables; one builder pipeline writes all of them):

  - analysis.pe + analysis.dividends (daily raw PE / trailing-12m
    dividend yield)
  - analysis.pe_pct (monthly trailing percentile bands of the pe
    series) + analysis.pe_pct_streaks (band-break excursion streaks
    audited against those bands — the mov_ave_high_low_pct[_streaks]
    pattern applied to the pe series)

(The former annual 10y rolling stats stage — analysis.pe_stats /
analysis.dividends_stats — and the dividend_yield percentile bands /
streaks half — analysis.dividends_pct / analysis.dividends_pct_streaks
— were removed 2026-09-28 with their only consumer, the UI's
PE-only Valuation Streaks card.)

Package layout:
  - _common/ — shared config (sec_type universe), the source-data fetch
    layer, and the combined detail-frame assembly
  - pe/ — PE compute (clean_pe) + the pe_pct[_streaks] bands/streaks
    pipeline + its constants
  - dividends/ — dividend-yield compute (trailing-12m DPS, index /
    etf / stock yields) + its constants

Run via ``python -m analysis.pe_and_dividends``.
"""
