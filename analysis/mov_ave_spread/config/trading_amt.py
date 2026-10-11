"""Trading-amount metric constants for analysis.mov_ave_spread.

analysis.mov_ave_trading_amt — trading-amount metrics extracted from
mov_ave_spreads_detail plus new rolling max/min and ratio columns.
Internal step of the parent mov_ave_spread pipeline (see
steps/trading_amt.py). Also carries the trading-amount column-name
constants shared by the detail-frame derivation (_common/derive.py),
the source fetch (fetch/source.py) and the detail build (steps/detail.py).
"""
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

# Trading-amount MA column names (in canonical window order). Source:
# stats.{etf_liquidity_margin,index_basic_stats,stock_liquidity_margin}
# .trading_amount. Computed per (sec_type, code) ordered by date with
# min_periods=W (NULL until W rows). NULL trading_amount values are
# treated as 0 (zero turnover) in the rolling sum but still counted in
# the W-row denominator — see derive.compute_trading_amt_mas for the
# rationale. Used by derive.compute_trading_amt_mas and
# detail._assemble_detail_columns.
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
# TRADING_AMT_MA_COLUMNS). Used by derive.compute_trading_amt_market_share_mas
# and detail._assemble_detail_columns.
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
# or ma[t-1] <= 0. Used by derive.compute_trading_amt_ma_slopes and
# detail._assemble_detail_columns.
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
# Used by steps/trading_amt.compute_trading_amt_slope.
TRADING_AMT_RAW_SLOPE_COLUMN = "trading_amt_slope"

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
# Used by derive.compute_trading_amt_market_share_vs_mas and
# detail._assemble_detail_columns. Must be called AFTER
# compute_trading_amt_market_share_mas (reads the ma columns).
TRADING_AMT_MARKET_SHARE_VS_MA_COLUMNS = (
    "trading_amt_market_share_vs_ma5",
    "trading_amt_market_share_vs_ma20",
    "trading_amt_market_share_vs_ma60",
    "trading_amt_market_share_vs_ma120",
    "trading_amt_market_share_vs_ma255",
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
# (see TRADING_AMT_RATIOS_COLUMNS in trading_amt_ratios.py) — the shapes of
# TRADING_AMT_COLUMNS and the CREATE TABLE in
# database/sql/analysis/mov_ave_spreads/05_mov_ave_trading_amt.sql
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
