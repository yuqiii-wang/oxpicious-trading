"""Trading-amount liquidity-impact RATIO constants for
analysis.mov_ave_spread.

analysis.mov_ave_trading_amt_ratios — capital-per-movement ratio columns:
how many millions of yuan of trading amount accompany one unit of price
movement (reciprocal of the Amihud illiquidity measure; higher = deeper
market). Internal step of the parent mov_ave_spread pipeline (see
steps/trading_amt_ratios.py).
"""
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
# Used by steps/trading_amt_ratios.
# compute_trading_amt_slope_vs_price_ratios (written to
# analysis.mov_ave_trading_amt_ratios).
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
# TABLE in database/sql/analysis/mov_ave_spreads/
# 06_mov_ave_trading_amt_ratios.sql — COPY infers columns from these
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
