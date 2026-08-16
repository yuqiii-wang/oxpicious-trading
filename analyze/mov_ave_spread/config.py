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

# stats.*_tech_stats EMA column names by EMA window. EMAs come from the same
# tech_stats tables as MAs (stats.{etf,index,stock}_tech_stats.ema{6,20,60,
# 120,255}). ema10 exists in the source tables but is not used by this
# analysis (the EMA detail table mirrors the MA window set: 6/20/60/120/255).
TECH_STATS_EMA_COLUMNS = {
    6:   "ema6",
    20:  "ema20",
    60:  "ema60",
    120: "ema120",
    255: "ema255",
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

# Wider bound for trading_amt_ma{5,20,60,120,255} columns, declared
# NUMERIC(24,4) — |value| < 10^(24-4) = 10^20 after rounding to 4 dp.
# Matches the source column precision (stats.{etf_liquidity_margin,
# index_basic_stats,stock_liquidity_margin}.trading_amount is NUMERIC(24,4))
# so broad-index daily turnover up to 10^20 yuan fits without overflow.
# Daily trading_amount for broad indices (e.g. SSE Composite) can reach
# 10^13+ yuan on busy days; NUMERIC(16,4) (cap 10^12) was found too tight
# and ~15% of index rows were overflow-nulled under that bound. Used by
# helpers.null_if_overflow when max_abs is overridden per-column.
NUMERIC_WIDE_MAX_ABS = 10**20

# Trading-amount MA column names (in canonical window order). Source:
# stats.{etf_liquidity_margin,index_basic_stats,stock_liquidity_margin}
# .trading_amount. Computed per (sec_type, code) ordered by date with
# min_periods=W (NULL until W rows). NULL trading_amount values are
# treated as 0 (zero turnover) in the rolling sum but still counted in
# the W-row denominator — see helpers.compute_trading_amt_mas for the
# rationale. Used by helpers.compute_trading_amt_mas and
# compute._assemble_detail_columns.
TRADING_AMT_MA_COLUMNS = (
    "trading_amt_ma5",
    "trading_amt_ma20",
    "trading_amt_ma60",
    "trading_amt_ma120",
    "trading_amt_ma255",
)

# Trading-amount MARKET-SHARE MA column names (in canonical window order).
# market_share[date, code] = trading_amount[date, code] / denominator[date]
# where denominator = SUM(stats.exchange_trading_amt.total_trading_amount)
# across exchanges whose stats.sec_classification.is_primary_exchange = TRUE.
# Then trading_amt_market_share_ma{W} = W-day MA of market_share per
# (sec_type, code) ordered by date with min_periods=W. NULL market_share
# treated as 0 in rolling mean, counted in denominator (same pattern as
# TRADING_AMT_MA_COLUMNS). Used by helpers.compute_trading_amt_market_share_mas
# and compute._assemble_detail_columns.
TRADING_AMT_MARKET_SHARE_MA_COLUMNS = (
    "trading_amt_market_share_ma5",
    "trading_amt_market_share_ma20",
    "trading_amt_market_share_ma60",
    "trading_amt_market_share_ma120",
    "trading_amt_market_share_ma255",
)

# Trading-amount MA SLOPE column names (in canonical window order). Each is
# a RATIO (fractional daily change) = (ma[t] - ma[t-1]) / ma[t-1], NOT a raw
# difference — so NUMERIC(10,4) is sufficient (typical |slope| < 0.1). NULL on
# the first date of each code (no prior row) or when ma[t]/ma[t-1] is NULL
# or ma[t-1] <= 0. Used by helpers.compute_trading_amt_ma_slopes and
# compute._assemble_detail_columns.
TRADING_AMT_MA_SLOPE_COLUMNS = (
    "trading_amt_ma5_slope",
    "trading_amt_ma20_slope",
    "trading_amt_ma60_slope",
    "trading_amt_ma120_slope",
    "trading_amt_ma255_slope",
)

# Trading-amount MARKET-SHARE-vs-MA gap column names (in canonical window
# order). Each is a signed fractional ratio:
#   trading_amt_market_share_vs_ma{W}[t] =
#       (market_share[t] - market_share_ma{W}[t]) / market_share_ma{W}[t]
# where market_share[t] = trading_amount[t] / denominator[t] (denominator =
# SUM of primary-exchange total_trading_amount on date t). Positive = the
# security's current market share is ABOVE its W-day average (gaining
# relative liquidity); negative = BELOW (losing relative liquidity).
# NUMERIC(10,4) is sufficient — typical |ratio| < 1.0. NULL when
# market_share or market_share_ma{W} is NULL or market_share_ma{W} <= 0.
# Used by helpers.compute_trading_amt_market_share_vs_mas and
# compute._assemble_detail_columns. Must be called AFTER
# compute_trading_amt_market_share_mas (reads the ma columns).
TRADING_AMT_MARKET_SHARE_VS_MA_COLUMNS = (
    "trading_amt_market_share_vs_ma5",
    "trading_amt_market_share_vs_ma20",
    "trading_amt_market_share_vs_ma60",
    "trading_amt_market_share_vs_ma120",
    "trading_amt_market_share_vs_ma255",
)


# ============================================================================
#  EMA detail (analysis.mov_ave_spreads_detail_ema)
#  — EMA counterpart of the MA detail table. Internal step of the parent
#  mov_ave_spread pipeline (see ema.py).
# ============================================================================

EMA_DETAIL_TABLE = "analysis.mov_ave_spreads_detail_ema"
EMA_ANALYSIS_NAME = "mov_ave_spread_ema"

# EMA windows for slope (1st derivative) and curvature (2nd derivative).
# Matches the ema{W}_slope / ema{W}_curvature columns in the EMA detail
# table. Source: stats.{etf,index,stock}_tech_stats.ema{6,20,60,120,255}.
EMA_WINDOWS = (6, 20, 60, 120, 255)

# 9 (num_col, den_col, gap_column_name) tuples in canonical order for the
# EMA detail table. num_col="price" is the price sentinel; num_col="ema6"
# uses ema6. gap_column_name matches the column in
# analysis.mov_ave_spreads_detail_ema.
EMA_PAIRS = [
    ("price", "ema6",   "price_vs_ema6"),
    ("price", "ema20",  "price_vs_ema20"),
    ("price", "ema60",  "price_vs_ema60"),
    ("price", "ema120", "price_vs_ema120"),
    ("price", "ema255", "price_vs_ema255"),
    ("ema6",  "ema20",  "ema6_vs_ema20"),
    ("ema6",  "ema60",  "ema6_vs_ema60"),
    ("ema6",  "ema120", "ema6_vs_ema120"),
    ("ema6",  "ema255", "ema6_vs_ema255"),
]

# EMA gap column names (canonical order). Used by ema.py for column
# selection + overflow guard.
EMA_VS_COLUMNS = (
    "price_vs_ema6",
    "price_vs_ema20",
    "price_vs_ema60",
    "price_vs_ema120",
    "price_vs_ema255",
    "ema6_vs_ema20",
    "ema6_vs_ema60",
    "ema6_vs_ema120",
    "ema6_vs_ema255",
)

# EMA slope column names (canonical window order). Each is a RAW difference
# (EMA[t] - EMA[t-1]) per (sec_type, code) ordered by date, so NUMERIC(10,6)
# overflow guard applies (same as ma{W}_slope).
EMA_SLOPE_COLUMNS = (
    "ema6_slope",
    "ema20_slope",
    "ema60_slope",
    "ema120_slope",
    "ema255_slope",
)

# EMA curvature column names (canonical window order). Each is the 2nd
# derivative (slope[t] - slope[t-1]).
EMA_CURVATURE_COLUMNS = (
    "ema6_curvature",
    "ema20_curvature",
    "ema60_curvature",
    "ema120_curvature",
    "ema255_curvature",
)

# Rolling population σ column names (Bollinger band widths) stored on the
# EMA detail table. Mirrors the SMA detail table's std_*days columns — same
# source data (σ of price over W days, ddof=0, computed by
# helpers.compute_rolling_stds in the parent pipeline) carried into the EMA
# table so it is self-contained for Bollinger rendering without a JOIN back
# to the SMA detail table.
#
# The column NAME uses the SMA window (5/20/60/120/255) — NOT the EMA window
# (6/20/60/120/255) — to match the SMA detail table's column names. For the
# EMA6 envelope, std_5days (5-day σ) is used as the closest available window
# (the 1-day difference vs the EMA6 window is negligible for σ). For all
# other EMA windows (20/60/120/255) the σ window matches the EMA window
# exactly.
EMA_STD_COLUMNS = (
    "std_5days",
    "std_20days",
    "std_60days",
    "std_120days",
    "std_255days",
)
