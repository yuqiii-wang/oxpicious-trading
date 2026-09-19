-- ============================================================================
--  mov_ave_spread suite — split of the former 03_mov_ave_spreads.sql.
--  Shared conventions (sec_type handling, the NO-FK derived-data design,
--  rebuild semantics): see 01_mov_ave_spreads_detail.sql.
-- ============================================================================

-- ----------------------------------------------------------------------------
--  Table: analysis.mov_ave_trading_amt  (WIDE format — trading-amount metrics)
--    One row per (sec_type, code, date). Liquidity-trend metrics computed
--    from the same source DataFrame as mov_ave_spreads_detail.
--
--  Columns (grouped by semantic purpose):
--
--  1. Trading-amount MAs (yuan, NUMERIC(24,4)):
--     trading_amt_ma{5,20,60,120,255} — simple moving average of
--     trading_amount per (sec_type, code). Source:
--     stats.{etf_liquidity_margin,index_basic_stats,stock_liquidity_margin}
--     .trading_amount. NULL until W consecutive rows.
--
--  2. Trading-amount Bollinger σ (yuan, NUMERIC(24,4)):
--     trading_amt_std{5,20,60,120,255} — rolling population σ (ddof=0) of
--     trading_amt_maW over W days. Bollinger-style envelope widths around
--     each trading-amount MA line. NULL until W rows.
--
--  3. Trading-amount market-share MAs (NUMERIC(10,4)):
--     trading_amt_market_share_ma{5,20,60,120,255} — market_share =
--     trading_amount / market_denominator. Then W-day MA of market_share
--     per (sec_type, code).
--
--  4. Trading-amount slopes (NUMERIC(10,4)):
--     trading_amt_slope — fractional daily change of RAW trading_amount.
--     trading_amt_ma{5,20,60,120,255}_slope — fractional daily change
--     (ma[t]-ma[t-1])/ma[t-1] of each MA.
--
--  5. Trading-amount market-share-vs-MA gaps (NUMERIC(10,4)):
--     trading_amt_market_share_vs_ma{5,20,60,120,255} — signed fractional
--     gap (market_share - market_share_ma{W}) / market_share_ma{W}.
--
--  The liquidity-impact RATIO columns (trading_amt_vs_price_slope_ratio
--  etc.) previously drafted for this table live in the companion table
--  analysis.mov_ave_trading_amt_ratios (see below).
--
--  Populated by the internal trading-amt step of `analyze.mov_ave_spread`
--  (see trading_amt.py). Incremental upsert by missing dates;
--  --force truncates first.
-- ----------------------------------------------------------------------------

DROP TABLE IF EXISTS analysis.mov_ave_trading_amt CASCADE;
DROP TABLE IF EXISTS analysis.mov_ave_trading_amt_ratios CASCADE;
DELETE FROM analysis.analysis_identity WHERE name = 'mov_ave_trading_amt';
DELETE FROM analysis.analysis_identity WHERE name = 'mov_ave_trading_amt_ratios';

CREATE TABLE analysis.mov_ave_trading_amt (
    sec_type        TEXT         NOT NULL,  -- 'etf' | 'index' | 'stock'
    code            TEXT         NOT NULL,
    date            DATE         NOT NULL,

    -- 5 trading-amount moving-average columns (yuan, NUMERIC(24,4) — matches
    -- the source column precision stats.{etf_liquidity_margin,index_basic_stats,
    -- stock_liquidity_margin}.trading_amount which is NUMERIC(24,4). Daily
    -- turnover for broad indices like SSE Composite can reach 10^13+ yuan,
    -- which would overflow NUMERIC(16,4) (cap 10^12)). Source: per (sec_type,
    -- code) ordered by date with min_periods=W so NULL until W consecutive
    -- rows are available. Used to gauge liquidity trend / capital-flow
    -- strength alongside the price-based gap columns.
    trading_amt_ma5     NUMERIC(24,4),
    trading_amt_ma20    NUMERIC(24,4),
    trading_amt_ma60    NUMERIC(24,4),
    trading_amt_ma120   NUMERIC(24,4),
    trading_amt_ma255   NUMERIC(24,4),

    trading_amt_std5     NUMERIC(24,4),
    trading_amt_std20    NUMERIC(24,4),
    trading_amt_std60    NUMERIC(24,4),
    trading_amt_std120   NUMERIC(24,4),
    trading_amt_std255   NUMERIC(24,4),

    trading_amt_market_share_ma5     NUMERIC(10,4),
    trading_amt_market_share_ma20    NUMERIC(10,4),
    trading_amt_market_share_ma60    NUMERIC(10,4),
    trading_amt_market_share_ma120   NUMERIC(10,4),
    trading_amt_market_share_ma255   NUMERIC(10,4),
 
    trading_amt_slope     NUMERIC(10,4),
    trading_amt_ma5_slope     NUMERIC(10,4),
    trading_amt_ma20_slope    NUMERIC(10,4),
    trading_amt_ma60_slope    NUMERIC(10,4),
    trading_amt_ma120_slope   NUMERIC(10,4),
    trading_amt_ma255_slope   NUMERIC(10,4),

    trading_amt_market_share_vs_ma5     NUMERIC(10,4),
    trading_amt_market_share_vs_ma20    NUMERIC(10,4),
    trading_amt_market_share_vs_ma60    NUMERIC(10,4),
    trading_amt_market_share_vs_ma120   NUMERIC(10,4),
    trading_amt_market_share_vs_ma255   NUMERIC(10,4),

    CONSTRAINT pk_mov_ave_trading_amt PRIMARY KEY (code, sec_type, date)
) PARTITION BY HASH (code);

-- Native hash partitions (16) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p15
SELECT public.create_hash_partitions('analysis', 'mov_ave_trading_amt', 16);

COMMENT ON TABLE  analysis.mov_ave_trading_amt              IS 'Trading-amount analysis (WIDE format): one row per (code, sec_type, date) with 5 trading-amount MA columns + 5 trading-amount Bollinger band σ columns (rolling population std of trading_amt_maW over W days) + 5 market-share MA columns + 6 slope columns (raw trading_amt_slope + 5 fractional MA slopes) + 5 market-share-vs-MA gap columns. sec_type ∈ {etf, index, stock}. Liquidity-impact ratio columns live in the companion table analysis.mov_ave_trading_amt_ratios.';
COMMENT ON COLUMN analysis.mov_ave_trading_amt.sec_type   IS 'Security type: etf (ETF), index (CSI-style index), or stock (individual equity).';
COMMENT ON COLUMN analysis.mov_ave_trading_amt.code         IS 'Ticker. ETFs use exchange suffix (e.g. "510050.SS"); indices use bare code (e.g. "000300").';
COMMENT ON COLUMN analysis.mov_ave_trading_amt.date         IS 'Business date (trading day).';
COMMENT ON COLUMN analysis.mov_ave_trading_amt.trading_amt_ma5    IS '5-trading-day moving average of trading_amount (yuan) per (sec_type, code). NUMERIC(24,4). NULL until 5 rows. NULL trading_amount values are treated as 0 (zero turnover) in the rolling sum but still counted in the W-row denominator.';
COMMENT ON COLUMN analysis.mov_ave_trading_amt.trading_amt_ma20   IS '20-trading-day moving average of trading_amount (yuan). NULL until 20 rows.';
COMMENT ON COLUMN analysis.mov_ave_trading_amt.trading_amt_ma60   IS '60-trading-day moving average of trading_amount (yuan). NULL until 60 rows.';
COMMENT ON COLUMN analysis.mov_ave_trading_amt.trading_amt_ma120  IS '120-trading-day moving average of trading_amount (yuan). NULL until 120 rows.';
COMMENT ON COLUMN analysis.mov_ave_trading_amt.trading_amt_ma255  IS '255-trading-day moving average of trading_amount (yuan). NULL until 255 rows.';
COMMENT ON COLUMN analysis.mov_ave_trading_amt.trading_amt_std5   IS 'Rolling population σ (ddof=0) of trading_amt_ma5 over 5 trading days. Bollinger band width for trading-amount MA5 envelope. NUMERIC(24,4) — yuan units match the MA columns. NULL until 5 consecutive rows.';
COMMENT ON COLUMN analysis.mov_ave_trading_amt.trading_amt_std20  IS 'Rolling population σ (ddof=0) of trading_amt_ma20 over 20 trading days. Bollinger band width for trading-amount MA20 envelope. NULL until 20 consecutive rows.';
COMMENT ON COLUMN analysis.mov_ave_trading_amt.trading_amt_std60  IS 'Rolling population σ (ddof=0) of trading_amt_ma60 over 60 trading days. Bollinger band width for trading-amount MA60 envelope. NULL until 60 consecutive rows.';
COMMENT ON COLUMN analysis.mov_ave_trading_amt.trading_amt_std120 IS 'Rolling population σ (ddof=0) of trading_amt_ma120 over 120 trading days. Bollinger band width for trading-amount MA120 envelope. NULL until 120 consecutive rows.';
COMMENT ON COLUMN analysis.mov_ave_trading_amt.trading_amt_std255 IS 'Rolling population σ (ddof=0) of trading_amt_ma255 over 255 trading days. Bollinger band width for trading-amount MA255 envelope. NULL until 255 consecutive rows.';
COMMENT ON COLUMN analysis.mov_ave_trading_amt.trading_amt_market_share_ma5    IS '5-trading-day MA of market_share (trading_amount / market_denominator).';
COMMENT ON COLUMN analysis.mov_ave_trading_amt.trading_amt_market_share_ma20   IS '20-trading-day MA of market_share.';
COMMENT ON COLUMN analysis.mov_ave_trading_amt.trading_amt_market_share_ma60   IS '60-trading-day MA of market_share.';
COMMENT ON COLUMN analysis.mov_ave_trading_amt.trading_amt_market_share_ma120  IS '120-trading-day MA of market_share.';
COMMENT ON COLUMN analysis.mov_ave_trading_amt.trading_amt_market_share_ma255  IS '255-trading-day MA of market_share.';
COMMENT ON COLUMN analysis.mov_ave_trading_amt.trading_amt_ma5_slope    IS 'Fractional daily change (ma5[t]-ma5[t-1])/ma5[t-1].';
COMMENT ON COLUMN analysis.mov_ave_trading_amt.trading_amt_ma20_slope   IS 'Fractional daily change of trading_amt_ma20.';
COMMENT ON COLUMN analysis.mov_ave_trading_amt.trading_amt_ma60_slope   IS 'Fractional daily change of trading_amt_ma60.';
COMMENT ON COLUMN analysis.mov_ave_trading_amt.trading_amt_ma120_slope  IS 'Fractional daily change of trading_amt_ma120.';
COMMENT ON COLUMN analysis.mov_ave_trading_amt.trading_amt_ma255_slope  IS 'Fractional daily change of trading_amt_ma255.';
COMMENT ON COLUMN analysis.mov_ave_trading_amt.trading_amt_slope         IS 'Fractional daily change of raw trading_amount: (ta[t]-ta[t-1])/ta[t-1]. NUMERIC(10,4). NULL on first date per code or when ta[t-1] is NULL or <= 0.';
COMMENT ON COLUMN analysis.mov_ave_trading_amt.trading_amt_market_share_vs_ma5    IS '(market_share - market_share_ma5) / market_share_ma5.';
COMMENT ON COLUMN analysis.mov_ave_trading_amt.trading_amt_market_share_vs_ma20   IS '(market_share - market_share_ma20) / market_share_ma20.';
COMMENT ON COLUMN analysis.mov_ave_trading_amt.trading_amt_market_share_vs_ma60   IS '(market_share - market_share_ma60) / market_share_ma60.';
COMMENT ON COLUMN analysis.mov_ave_trading_amt.trading_amt_market_share_vs_ma120  IS '(market_share - market_share_ma120) / market_share_ma120.';
COMMENT ON COLUMN analysis.mov_ave_trading_amt.trading_amt_market_share_vs_ma255  IS '(market_share - market_share_ma255) / market_share_ma255.';
