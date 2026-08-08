"""Configuration constants for analyze.mov_ave_spread.

Moving-Average Spread Analysis (ETF + Index + Stock).

Loads every business date's MA-gap values for every ETF, every index, and
every stock into the wide-format detail table analysis.mov_ave_spreads_detail.

The ``sec_type`` column discriminates the source universe. The schema
CHECK allows three values ('etf' | 'index' | 'stock'):
  - ETF   - price = COALESCE(stats.etf_adjustment.adj_close,
                             stats.etf_basic_stats.close);
            MAs from stats.etf_tech_stats.
  - Index - price = stats.index_basic_stats.close (indices have no
            adjustment table); MAs from stats.index_tech_stats.
  - Stock - price = stats.stock_basic_stats.close (no adjustment table);
            MAs from stats.stock_tech_stats (populated by
            builds.stock.tech_stats).

9 gap pairs (canonical order):
  - 5 Price-vs-MA pairs:  gap = (price - maX) / maX,  X in {5,20,60,120,255}
  - 4 MA5-vs-MA pairs:    gap = (ma5  - maX) / maX,  X in {20,60,120,255}
"""
ANALYSIS_NAME = "mov_ave_spread"
DETAIL_TABLE = "analysis.mov_ave_spreads_detail"
PEAKS_AND_FLOORS_TABLE = "analysis.mov_ave_peaks_and_floors"

DESCRIPTION = (
    "Moving-average spread analysis (ETF + Index + Stock). For each security "
    "(ETF, index, or stock) and business date, computes 9 gap pairs (5 "
    "Price/MA + 4 MA5/MA) as gap_value = (short_value - long_value) / "
    "long_value, plus 1st derivative (slope) and 2nd derivative (curvature) "
    "of price and each MA (ma5 / ma20 / ma60 / ma120 / ma255) computed per "
    "code ordered by date, plus 5 rolling population σ columns (std_5days / "
    "std_20days / std_60days / std_120days / std_255days) used for "
    "Bollinger-style envelopes (MA ± k×σ) around each Price/MA pair chart. "
    "The sec_type column discriminates the source universe; the schema "
    "CHECK allows 'etf' | 'index' | 'stock'. 'etf' uses "
    "COALESCE(etf_adjustment.adj_close, etf_basic_stats.close) for price "
    "and etf_tech_stats for MAs; 'index' uses index_basic_stats.close for "
    "price and index_tech_stats for MAs; 'stock' uses stock_basic_stats.close "
    "for price and stock_tech_stats for MAs. Detail table stores one wide "
    "row per (sec_type, code, date) with all 9 gap values + 12 "
    "slope/curvature columns (price + 5 MAs x slope/curv) + 5 rolling σ "
    "columns."
)

# stats.*_tech_stats column names by MA window (identical for etf and index).
TECH_STATS_MA_COLUMNS = {
    5:   "ma5",
    20:  "ma20",
    60:  "ma60",
    120: "ma120",
    255: "ma255",
}

# MA windows for which slope (1st derivative) and curvature (2nd derivative)
# are computed. Matches the ma{W}_slope / ma{W}_curvature columns in the
# detail table.
MA_WINDOWS = (5, 20, 60, 120, 255)

# 9 (ma_short, ma_long, gap_column_name) tuples in canonical order.
# ma_short = 0 is the price sentinel (short_value = price); ma_short = 5
# uses ma5. gap_column_name matches the column in
# analysis.mov_ave_spreads_detail.
PAIRS = [
    (0, 5,   "price_vs_ma5"),
    (0, 20,  "price_vs_ma20"),
    (0, 60,  "price_vs_ma60"),
    (0, 120, "price_vs_ma120"),
    (0, 255, "price_vs_ma255"),
    (5, 20,  "ma5_vs_ma20"),
    (5, 60,  "ma5_vs_ma60"),
    (5, 120, "ma5_vs_ma120"),
    (5, 255, "ma5_vs_ma255"),
]

# Security types computed by this script. The DB schema CHECK on
# analysis.mov_ave_spreads_detail.sec_type allows ('etf', 'index', 'stock').
# Stock MAs come from stats.stock_tech_stats (populated by
# builds.stock.tech_stats); stock prices use stats.stock_basic_stats.close
# (no adjustment table for stocks).
SEC_TYPES = ("etf", "index", "stock")

# Identity table per sec_type - used by the recent-data pre-filter
# (fetch_codes_with_recent_data_async) to find codes with at least one row
# in the last RECENT_TRADING_DAYS trading days. A code with no recent data
# (delisted / suspended / never-traded) is excluded from the analysis
# universe entirely so its full history is skipped.
SEC_TYPE_IDENTITY_TABLE = {
    "etf":   "stats.etf_identity",
    "index": "stats.index_identity",
    "stock": "stats.stock_identity",
}

# Every numeric column in analysis.mov_ave_spreads_detail is declared
# NUMERIC(10,6), whose absolute value must be < 10^4 (= 10000) after
# rounding to 6 decimal places - otherwise PostgreSQL raises
# NumericValueOutOfRangeError on insert. The gap columns (price_vs_maX,
# ma5_vs_maX) are ratios and stay well under this bound in practice; the
# slope/curvature columns are RAW differences (MA[t] - MA[t-1]) and can
# exceed 10000 for high-priced ETFs/indices at corporate-action or
# source-data-unit boundaries. Values at or beyond this bound are nulled
# before insert by _null_if_overflow rather than dropped, so the row is
# still written with its other (valid) columns.
NUMERIC_MAX_ABS = 10000.0
