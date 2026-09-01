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

# Trading-amount RAW (no-MA) SLOPE column. Fractional daily change of
# raw trading_amount: slope[t] = (ta[t] - ta[t-1]) / ta[t-1]. Same
# formula as TRADING_AMT_MA_SLOPE_COLUMNS but on the raw value instead
# of an MA. NUMERIC(10,4) — typical |slope| < 0.5 for broad indices.
# NULL on first date of each code or when ta[t]/ta[t-1] is NULL or ta[t-1] <= 0.
# Used by trading_amt.compute_trading_amt_slope.
TRADING_AMT_RAW_SLOPE_COLUMN = "trading_amt_slope"

# Trading-amount vs PRICE SLOPE RATIO column names (6 columns).
# Each is a liquidity-impact proxy = (trading_amt / 1_000_000) / price_slope.
# Trading amount is divided by 1M to express capital in millions (yuan).
#   col[0] = (trading_amount / 1M) / price_slope       (raw vs raw)
#   col[1] = (trading_amt_ma5 / 1M) / ma5_slope
#   col[2] = (trading_amt_ma20 / 1M) / ma20_slope
#   col[3] = (trading_amt_ma60 / 1M) / ma60_slope
#   col[4] = (trading_amt_ma120 / 1M) / ma120_slope
#   col[5] = (trading_amt_ma255 / 1M) / ma255_slope
# Interpretation: how many millions of capital accompany one unit of
# price movement — the reciprocal of the Amihud (2002) illiquidity
# measure (higher = deeper market). Matching-timescale (no
# cross-timescale). Denominator=0 auto-set to 1.0 to avoid
# division-by-zero. NUMERIC(10,4). NULL when numerator or denominator
# is NULL.
# Used by trading_amt_ratios.compute_trading_amt_slope_vs_price_ratios
# (written to analysis.mov_ave_trading_amt_ratios).
TRADING_AMT_SLOPE_VS_PRICE_RATIO_COLUMNS = (
    "trading_amt_vs_price_slope_ratio",
    "trading_amt_ma5_vs_price_ma5_slope_ratio",
    "trading_amt_ma20_vs_price_ma20_slope_ratio",
    "trading_amt_ma60_vs_price_ma60_slope_ratio",
    "trading_amt_ma120_vs_price_ma120_slope_ratio",
    "trading_amt_ma255_vs_price_ma255_slope_ratio",
)

# Source price-slope column names needed by the ratio computation.
# These come from compute_slopes_curvatures in the parent pipeline.
# Used by trading_amt_ratios (raw diffs of price and each MA window).
TRADING_AMT_PRICE_SLOPE_SOURCE_COLUMNS = (
    "price_slope",
    "ma5_slope",
    "ma20_slope",
    "ma60_slope",
    "ma120_slope",
    "ma255_slope",
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


# ============================================================================
#  OHLC detail (analysis.mov_ave_spreads_detail_ohlc)
#  — Rolling OHLC summary per window. Internal step of the parent
#  mov_ave_spread pipeline (see ohlc.py).
# ============================================================================

OHLC_TABLE = "analysis.mov_ave_spreads_detail_ohlc"
OHLC_ANALYSIS_NAME = "mov_ave_spread_ohlc"

# OHLC windows (trading days). Each window W produces 5 columns:
#   open_Wd      — open price on the W-th trading day before `date`
#   high_Wd      — top-high anchor: the MAXIMUM valid CLOSE in the 1st
#                  HALF of the window; the stored value is that anchor
#                  date's CLOSE
#   low_Wd       — top-low anchor: the MINIMUM valid CLOSE in the 1st
#                  half; the stored value is that anchor date's CLOSE
#   high_2nd_Wd  — second-high anchor: the MAXIMUM valid CLOSE in the
#                  2nd HALF of the window; the stored value is that
#                  anchor date's INTRADAY HIGH
#   low_2nd_Wd   — second-low anchor: the MINIMUM valid CLOSE in the
#                  2nd half; the stored value is that anchor date's
#                  INTRADAY LOW
# HALF-SPLIT ANCHORS: the window [date-W+1, date] is cut in half —
# h = L // 2 with L the window length in trading-day positions (for odd
# L the 2nd half gets the extra day); the 1st extreme is the max/min
# valid CLOSE of the 1st half and the 2nd extreme the max/min valid
# CLOSE of the 2nd half. Ties go to the earliest date; NaN closes are
# skipped. The halves are disjoint and ordered, so the 2nd anchor date
# is ALWAYS strictly after the 1st anchor date wherever both exist.
# The columns are NULL when the window has fewer than 2 positions or
# the half holds no valid close.
OHLC_WINDOWS = (20, 60, 120, 255, 500, 750, 1275)

# DB schema (LONG format): one row per (sec_type, code, date, period) —
# the per-period columns in table order (after the PK columns sec_type,
# code, date). period ∈ OHLC_WINDOWS. The internal compute pipeline keeps
# the WIDE per-window layout (OHLC_WIDE_COLUMNS below, one column set per
# window on a (sec_type, code, date) frame) and melts to this long schema
# only at the DB-write boundary (ohlc.build_ohlc_long_frame).
OHLC_COLUMNS = (
    "today_close",
    "open_over_period",
    "high_over_period",
    "high_date_over_period",
    "low_over_period",
    "low_date_over_period",
    "high_2nd_over_period",
    "high_2nd_date_over_period",
    "low_2nd_over_period",
    "low_2nd_date_over_period",
    "high_line_slope_over_period",
    "low_line_slope_over_period",
)


# Internal WIDE per-window compute column names (pre-melt) for window W:
#   open_Wd, high_Wd, high_date_Wd, low_Wd, low_date_Wd,
#   high_2nd_Wd, high_2nd_date_Wd, low_2nd_Wd, low_2nd_date_Wd,
#   high_line_slope_Wd, low_line_slope_Wd
def ohlc_wide_columns(w: int) -> tuple:
    return (
        f"open_{w}d", f"high_{w}d", f"high_date_{w}d",
        f"low_{w}d", f"low_date_{w}d",
        f"high_2nd_{w}d", f"high_2nd_date_{w}d",
        f"low_2nd_{w}d", f"low_2nd_date_{w}d",
        f"high_line_slope_{w}d", f"low_line_slope_{w}d",
    )


OHLC_WIDE_COLUMNS = tuple(
    c for w in OHLC_WINDOWS for c in ohlc_wide_columns(w)
)

# DATE-type subsets. Used by the melt / sanitize logic to skip numeric
# conversion for the anchor-date columns.
OHLC_DATE_COLUMNS = tuple(
    c for c in OHLC_COLUMNS if "_date_" in c
)
OHLC_WIDE_DATE_COLUMNS = tuple(
    c for c in OHLC_WIDE_COLUMNS if "_date_" in c
)

# Numeric (NUMERIC(18,6)) columns of the wide internal layout.
OHLC_WIDE_NUMERIC_COLUMNS = tuple(
    c for c in OHLC_WIDE_COLUMNS
    if c not in OHLC_WIDE_DATE_COLUMNS
)

# Numeric (NUMERIC(18,6)) columns of the long schema.
OHLC_NUMERIC_COLUMNS = tuple(
    c for c in OHLC_COLUMNS
    if c not in OHLC_DATE_COLUMNS
)

OHLC_DESCRIPTION = (
    "OHLC detail analysis (ETF + Index + Stock), LONG format: one row "
    "per (security, business date, period) with period ∈ {20, 60, 120, "
    "255, 500, 750, 1275} trading days. Stores today_close plus the "
    "rolling-window anchors for that period. The window "
    "[date-period+1, date] is cut in HALF (h = L // 2 in trading-day "
    "positions; for odd L the 2nd half gets the extra day). "
    "high_over_period / low_over_period are the top anchors: the "
    "MAXIMUM / MINIMUM valid CLOSE of the 1st half (ties -> earliest "
    "date; value = that date's close). "
    "high_2nd_over_period / low_2nd_over_period are the second anchors: "
    "the MAXIMUM / MINIMUM valid CLOSE of the 2nd half (value = that "
    "date's INTRADAY high/low). The halves are disjoint and ordered, so "
    "the 2nd anchor date is ALWAYS strictly after the 1st anchor date "
    "wherever both exist. NaN closes are skipped; a half with no valid "
    "close NULLs its anchor, and windows with fewer than 2 positions "
    "have no anchors. The "
    "columns (high_date_over_period, "
    "low_date_over_period, high_2nd_date_over_period, "
    "low_2nd_date_over_period) record the anchor dates. Source: same "
    "DataFrame as the mov_ave_spread parent pipeline (no second DB "
    "round-trip). The sec_type column discriminates the source universe "
    "('etf' | 'index' | 'stock')."
)


# ============================================================================
#  Trading-amount detail (analysis.mov_ave_trading_amt)
#  — Trading-amount metrics extracted from mov_ave_spreads_detail plus
#  new rolling max/min and ratio columns. Internal step of the parent
#  mov_ave_spread pipeline (see trading_amt.py).
# ============================================================================

TRADING_AMT_TABLE = "analysis.mov_ave_trading_amt"
TRADING_AMT_ANALYSIS_NAME = "mov_ave_trading_amt"

TRADING_AMT_DESCRIPTION = (
    "Trading-amount analysis (ETF + Index + Stock). For each security "
    "and business date, computes 5 trading-amount MA columns "
    "(trading_amt_ma{5,20,60,120,255}), 5 trading-amount Bollinger band σ "
    "columns (trading_amt_std{5,20,60,120,255} — rolling population std of "
    "trading_amt_maW over W days, used for Bollinger-style envelopes), "
    "5 market-share MA columns, 6 slope columns (raw trading_amt_slope + "
    "5 fractional MA slopes), and 5 market-share-vs-MA gap columns. "
    "Source: same DataFrame "
    "as the mov_ave_spread parent pipeline (no second DB round-trip). "
    "The sec_type column discriminates the source universe "
    "('etf' | 'index' | 'stock')."
)

# Trading-amount Bollinger band σ column names (rolling population std of
# trading_amt_maW over W days, ddof=0). Used for Bollinger-style envelopes
# (MA ± k×σ) around each trading-amount MA line. NUMERIC(24,4) matches the
# MA column precision (yuan units). Computed per (sec_type, code) ordered
# by date with min_periods=W (NULL until W consecutive rows).
TRADING_AMT_STD_COLUMNS = (
    "trading_amt_std5",
    "trading_amt_std20",
    "trading_amt_std60",
    "trading_amt_std120",
    "trading_amt_std255",
)

# All output column names in the order they appear in the table.
# NOTE: the 6 liquidity-impact ratio columns previously drafted for this
# table live in the companion table analysis.mov_ave_trading_amt_ratios
# (see TRADING_AMT_RATIOS_COLUMNS below) — the shapes of
# TRADING_AMT_COLUMNS and the CREATE TABLE in 03_mov_ave_spreads.sql
# must stay in sync.
TRADING_AMT_COLUMNS = (
    ("sec_type", "code", "date")
    + TRADING_AMT_MA_COLUMNS
    + TRADING_AMT_STD_COLUMNS
    + TRADING_AMT_MARKET_SHARE_MA_COLUMNS
    + TRADING_AMT_MA_SLOPE_COLUMNS
    + (TRADING_AMT_RAW_SLOPE_COLUMN,)
    + TRADING_AMT_MARKET_SHARE_VS_MA_COLUMNS
)


# ============================================================================
#  Trading-amount liquidity-impact ratios
#  (analysis.mov_ave_trading_amt_ratios) — capital-per-movement ratio
#  columns: how many millions of yuan of trading amount accompany one
#  unit of price movement (reciprocal of the Amihud illiquidity
#  measure; higher = deeper market). Internal step of the parent
#  mov_ave_spread pipeline (see trading_amt_ratios.py).
# ============================================================================

TRADING_AMT_RATIOS_TABLE = "analysis.mov_ave_trading_amt_ratios"
TRADING_AMT_RATIOS_ANALYSIS_NAME = "mov_ave_trading_amt_ratios"

TRADING_AMT_RATIOS_DESCRIPTION = (
    "Trading-amount liquidity-impact ratios (ETF + Index + Stock). For "
    "each security and business date, computes 10 capital-per-movement "
    "ratio columns = (trading amount in millions of yuan) / (price "
    "movement in price units) — the reciprocal of the Amihud (2002) "
    "illiquidity measure: higher = deeper market. The daily price move "
    "decomposes into three legs, each with its own ratio family: "
    "close-to-close net move (6 slope ratios: trading_amt_vs_price_slope_"
    "ratio + trading_amt_ma{W}_vs_price_ma{W}_slope_ratio, matching "
    "timescale, signed), intraday range (trading_amt_vs_high_low_ratio = "
    "(ta/1M)/(high-low), unsigned depth gauge) and overnight gap "
    "(trading_amt_vs_overnight_gap_ratio = (ta/1M)/(open - prev close), "
    "signed gap-day liquidity gauge; the draft's literal 'prev close vs "
    "today close' reading would be identical to price_slope, so the "
    "standard trading 'gap' — where today's session OPENS relative to "
    "yesterday's close — is used instead), plus MA5-timescale versions "
    "of the range and gap ratios (numerators trading_amt_ma5, "
    "denominators MA5 of the daily range / gap). Denominator=0 auto-set "
    "to 1.0 (stored value = capital in millions) for flat / limit-locked "
    "days. Source: same DataFrame as the mov_ave_spread parent pipeline "
    "(no second DB round-trip). The sec_type column discriminates the "
    "source universe ('etf' | 'index' | 'stock')."
)

# High-low range ratio column (raw timescale). Capital per unit of
# INTRADAY range: (trading_amount / 1M) / (high - low). Unsigned and
# always positive (range > 0 unless limit-locked / flat). Range-based
# liquidity in the Parkinson-volatility spirit: high turnover + narrow
# range = deep book; low turnover + wide range = volatile / thin
# session. range=0 auto-set denominator to 1.0. NUMERIC(10,4).
TRADING_AMT_HIGH_LOW_RATIO_COLUMN = "trading_amt_vs_high_low_ratio"

# Overnight-gap ratio column (raw timescale). Capital per unit of
# OVERNIGHT gap: (trading_amount / 1M) / (open[t] - close[t-1]).
# Signed: negative = gap down. Gap-day liquidity / confirmation gauge.
# The literal "gap between prev close and today close" (close[t] -
# close[t-1]) is EXACTLY price_slope — already covered by
# trading_amt_vs_price_slope_ratio — so this column uses the standard
# trading "gap" (open vs prev close) instead, the distinct quantity.
# gap=0 auto-set denominator to 1.0. NULL on the first date per code
# (no prior close). NUMERIC(10,4).
TRADING_AMT_OVERNIGHT_GAP_RATIO_COLUMN = "trading_amt_vs_overnight_gap_ratio"

# MA5-timescale range ratio column. Matching timescale:
# (trading_amt_ma5 / 1M) / MA5(high - low) — 5-day average capital per
# unit of 5-day average daily range. NULL until 5 consecutive rows.
# NUMERIC(10,4).
TRADING_AMT_MA5_HIGH_LOW_RATIO_COLUMN = "trading_amt_ma5_vs_high_low_ma5_ratio"

# MA5-timescale overnight-gap ratio column. Matching timescale:
# (trading_amt_ma5 / 1M) / MA5(open[t] - close[t-1]) — 5-day average
# capital per unit of 5-day average overnight gap. NULL until 5
# consecutive gap observations. NUMERIC(10,4).
TRADING_AMT_MA5_OVERNIGHT_GAP_RATIO_COLUMN = (
    "trading_amt_ma5_vs_overnight_gap_ma5_ratio"
)

# All output column names of analysis.mov_ave_trading_amt_ratios in the
# order they appear in the table (must stay in sync with the CREATE
# TABLE in 03_mov_ave_spreads.sql — COPY infers columns from these
# dict keys).
TRADING_AMT_RATIOS_COLUMNS = (
    ("sec_type", "code", "date")
    + TRADING_AMT_SLOPE_VS_PRICE_RATIO_COLUMNS
    + (
        TRADING_AMT_HIGH_LOW_RATIO_COLUMN,
        TRADING_AMT_OVERNIGHT_GAP_RATIO_COLUMN,
        TRADING_AMT_MA5_HIGH_LOW_RATIO_COLUMN,
        TRADING_AMT_MA5_OVERNIGHT_GAP_RATIO_COLUMN,
    )
)

# Overflow bound for the ratio columns: NUMERIC(10,4) holds
# |value| < 10^6 (10 total digits - 4 decimals = 6 integer digits).
# Typical magnitudes: ~10^2 (small stocks) to ~10^5 (broad indices /
# liquid ETFs, e.g. 5e11-yuan index turnover / 30-pt move ~= 16,667).
# NOTE: the previous guard for these columns used 10^4 — the
# NUMERIC(10,6) bound copy-pasted over — which nulled exactly the most
# liquid instruments (broad indices ~16,667). This bound fixes that.
TRADING_AMT_RATIOS_MAX_ABS = 10**6


# ============================================================================
#  Holiday / non-trading-day risk (analysis.mov_ave_rsi_holiday)
#  — Captures previous-day trading/weekend/holiday status + today's
#    intraday gaps. Internal step of the parent mov_ave_spread pipeline
#    (see holiday.py).
# ============================================================================

HOLIDAY_TABLE = "analysis.mov_ave_rsi_holiday"
HOLIDAY_ANALYSIS_NAME = "mov_ave_rsi_holiday"

HOLIDAY_DESCRIPTION = (
    "Non-trading-day risk analysis (ETF + Index + Stock). For each trading "
    "day D, captures whether the previous calendar day (D-1) was a trading "
    "day / weekend / holiday / long holiday (>= 3 consecutive non-trading "
    "days including at least one official holiday), the consecutive "
    "non-trading-day count ending on D-1, and today's intraday high-low "
    "gap ((high-low)/close) and open-close gap ((close-open)/open). "
    "Source: same DataFrame as the mov_ave_spread parent pipeline "
    "(price, open, high, low columns). Holiday classification uses "
    "the project calendar (_common._holidays_and_weekdays). The sec_type "
    "column discriminates the source universe ('etf' | 'index' | 'stock')."
)

# All holiday column names in order.
HOLIDAY_COLUMNS = (
    "sec_type", "code", "date",
    "is_prev_day_trading",
    "is_prev_day_weekend",
    "is_prev_day_holiday",
    "is_prev_day_long_holiday",
    "non_trading_day_count",
    "today_high_low_gap",
    "today_open_close_gap",
)


# ============================================================================
#  Market hypes (analysis.mov_ave_market_hypes)
#  — Market-hype EPISODE detector: one row per CONCATENATED hype episode
#    per check-in window. An episode is a maximal span of trading dates
#    around a sustained run of hyped dates, extended through the
#    surrounding check-in evidence, with its SPAN (hype_days) bounded
#    below by the window (min_checkin_period) and above by the NEXT
#    window (exclusive) — so each calendar turmoil lands in exactly the
#    bucket matching its length. Internal step of the parent
#    mov_ave_spread pipeline (see market_hypes.py).
# ============================================================================

MARKET_HYPES_TABLE = "analysis.mov_ave_market_hypes"
MARKET_HYPES_ANALYSIS_NAME = "mov_ave_market_hypes"

# Check-in windows (trading rows) — one EPISODE SET per window per
# (sec_type, code). min_checkin_period IS the MINIMUM episode span for
# its bucket; the span is bounded above by the NEXT window (exclusive).
# Mirrors the min_checkin_period column (part of the PK) in
# 03_mov_ave_spreads.sql.
HYPE_CHECKIN_PERIODS = (5, 20, 60, 120, 255)

# Audit base window (trading rows) for the percentile thresholds:
# CENTERED ±10 trading years around each audited date — NOT a trailing /
# rolling-back window. The base for date t spans the 2550 rows (10 trading
# years) BEFORE t, t itself, and the 2550 rows AFTER t (total 5101 rows ≈
# 20 trading years). Windows near the start / end of a code's history are
# naturally truncated (the newest dates have no future rows yet — their
# base is effectively the trailing 10y); a base with fewer than
# HYPE_THRESHOLD_MIN_PERIODS non-NULL observations has no thresholds
# (the date is not hyped).
HYPE_THRESHOLD_HALF_WINDOW_ROWS = 2550
HYPE_THRESHOLD_WINDOW_ROWS = 2 * HYPE_THRESHOLD_HALF_WINDOW_ROWS + 1
HYPE_THRESHOLD_MIN_PERIODS = 255

# Maximum episode span for the LONGEST bucket (255d): the whole
# 10y+10y = 20y centered threshold base (2 * 2550 rows). Shorter buckets
# are capped by the next check-in window instead (see
# HYPE_EPISODE_SPAN_MAX) — e.g. a 20d-bucket episode spans 20..59 rows,
# a 60d-bucket one 60..119, and a 255d-bucket one 255..5100.
HYPE_MAX_EPISODE_ROWS = 2 * HYPE_THRESHOLD_HALF_WINDOW_ROWS

# Episode-span upper bound (EXCLUSIVE) per check-in window: the next
# window in HYPE_CHECKIN_PERIODS, or HYPE_MAX_EPISODE_ROWS (the full
# 20y base) for the longest window. Together with the window itself
# (the inclusive lower bound) this partitions episode lengths into
# disjoint buckets — one calendar turmoil lands in exactly the bucket
# whose range contains its span.
HYPE_EPISODE_SPAN_MAX = {
    w: (
        HYPE_CHECKIN_PERIODS[i + 1]
        if i + 1 < len(HYPE_CHECKIN_PERIODS)
        else HYPE_MAX_EPISODE_ROWS
    )
    for i, w in enumerate(HYPE_CHECKIN_PERIODS)
}

# Parameter set recorded on every row (the schema defaults). All three
# are strict-greater-than comparisons in percent units (0-100):
#   - satisfaction: fraction of check-in dates within the window that
#     must be EXCEEDED for is_hyped = TRUE (60.0 = "> 60% of the days").
#   - amt percentile: centered-20y (±10y) percentile of daily
#     trading_amount that a date must EXCEED on the liquidity leg
#     (60.0 = 60th pct).
#   - std percentile: centered-20y (±10y) percentile of std_{W}days
#     that a date must EXCEED on the volatility leg. Deliberately LOW
#     (30.0 = 30th pct): the W-day trailing σ lags a sudden turmoil by
#     construction (the window still holds W-1 pre-turmoil rows on day
#     1), so the volatility leg must clear a modest bar to let episodes
#     start at the turmoil's first big-move day — the 2024-09-24 rally
#     audit (159673.SZ) showed a 60th-pct std leg delayed episode starts
#     by a full month while the amt leg fired from day one.
# Changing any of these requires a --force rebuild (they are recorded
# per row but are NOT part of the PK).
HYPE_CHECKIN_SATISFACTION_THRESHOLD = 60.0
HYPE_TRADING_AMT_THRESHOLD_PCT = 60.0
HYPE_STD_THRESHOLD_PCT = 30.0

# Volatility source column per check-in window (matching timescale):
# the W-day rolling population σ of price already computed by the
# parent pipeline (helpers.compute_rolling_stds -> std_{W}days in the
# source DataFrame / mov_ave_spreads_detail).
HYPE_STD_COLUMN_BY_PERIOD = {
    5:   "std_5days",
    20:  "std_20days",
    60:  "std_60days",
    120: "std_120days",
    255: "std_255days",
}

# All output column names of analysis.mov_ave_market_hypes in the order
# they appear in the table (must stay in sync with the CREATE TABLE in
# 03_mov_ave_spreads.sql — COPY inserts with this explicit column
# order). NOTE: the PK is (sec_type, code, start_date, end_date,
# min_checkin_period); the three threshold columns are recorded build
# parameters, not key columns. trading_amt_hype_days / std_hype_days
# count the days within the episode span on which each leg individually
# checked in (diagnostics for which leg drove the episode).
MARKET_HYPES_COLUMNS = (
    "sec_type", "code",
    "start_date", "end_date", "min_checkin_period", "hype_days",
    "min_checkin_satisfaction_threshold",
    "min_trading_amt_threshold",
    "trading_amt_hype_days",
    "min_std_threshold",
    "std_hype_days",
)

MARKET_HYPES_DESCRIPTION = (
    "Market-hype EPISODE detector (ETF + Index + Stock). One row per "
    "(sec_type, code, min_checkin_period, episode): a CONCATENATED hype "
    "episode — a maximal span of trading dates anchored on a maximal run "
    "of consecutive hyped dates and extended through the surrounding "
    "check-in evidence (the W rows before the run's first hyped date, "
    "back to its first check-in, and the W rows after the last hyped "
    "date, to its last check-in). start_date / end_date bracket the "
    "span; hype_days = the span length in trading dates. min_checkin_"
    "period (W) is the bucket's MINIMUM span and the next window its "
    "EXCLUSIVE maximum (20d bucket: 20..59 rows; 60d: 60..119; 120d: "
    "120..254; 255d: 255..5100 = the whole ±10y threshold base), so "
    "each calendar turmoil lands in exactly the bucket matching its "
    "length. A date is hyped when, within the last W trading rows "
    "ending at it, MORE than min_checkin_satisfaction_threshold "
    "percent of the dates are check-ins — a check-in being a date "
    "whose daily trading_amount EXCEEDS its centered-20y "
    "min_trading_amt_threshold percentile AND whose W-day rolling "
    "population σ (std_{W}days, matching timescale) EXCEEDS its "
    "centered-20y min_std_threshold percentile. The audit base window "
    "is CENTERED on each audited date — 2550 trading rows (10 trading "
    "years) before the date plus 2550 rows after it (NOT a trailing/"
    "rolling-back window) — with a 255-row (1 trading year) minimum "
    "before thresholds exist; bases near the start/end of a code's "
    "history are naturally truncated (the newest dates have no future "
    "rows yet). Because the base looks both ways, historical rows use "
    "their following decade (retrospective audit; run --force to "
    "refresh historical rows' flags after new data arrives). "
    "trading_amt_hype_days / std_hype_days count the days within the "
    "episode span on which each leg individually checked in. Non-hyped "
    "dates leave no footprint; episodes are REBUILT WHOLESALE per "
    "sec_type on every pipeline run (new dates shift episode "
    "boundaries — the margin_changes precedent). One episode set per "
    "check-in window (5/20/60/120/255); the three threshold columns "
    "record the build's parameter set (defaults 60.0/60.0/30.0). "
    "Source: same DataFrame as the mov_ave_spread parent pipeline "
    "(trading_amount + std_{W}days columns — no second DB round-trip). "
    "The sec_type column discriminates the source universe ('etf' | "
    "'index' | 'stock')."
)
