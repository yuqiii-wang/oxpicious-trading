-- ============================================================================
--  Table: analysis.mov_ave_spreads_detail   (WIDE: one row per asset+code+date,
--                                             9 gap columns)
--
--  Supports multiple security types via the `sec_type` column
--  ('etf' | 'index' | 'stock'). ETF prices use COALESCE(stats.etf_adjustment.adj_close,
--  stats.etf_basic_stats.close); index prices use stats.index_basic_stats.close
--  (indices have no adjustment table); stock prices use stats.stock_basic_stats.close
--  (stock_adjustment / stock_tech_stats not yet available — stock rows will be
--  populated once those tables exist). MAs come from the corresponding
--  *_tech_stats table (ma5 / ma20 / ma60 / ma120 / ma255).
--
--  9 gap pairs (canonical order):
--    5 Price-vs-MA pairs:  gap = (price - maX) / maX,  X ∈ {5,20,60,120,255}
--    4 MA5-vs-MA pairs:    gap = (ma5  - maX) / maX,  X ∈ {20,60,120,255}
--
--  Detail table stores one row per (sec_type, code, date) with all 9 gap
--  values in wide form.
--
--  Repopulated from scratch on every run of
--  `analyze_mov_ave_spread.py` (TRUNCATE then INSERT).
--
--  NOTE: NO FOREIGN KEYS / NO CHECK CONSTRAINTS by design. These tables
--  hold derived data owned and rebuilt by `analyze.mov_ave_spread`, so
--  each keeps only its PRIMARY KEY for row identity / upserts. (An
--  earlier revision carried a polymorphic FK pattern — generated
--  etf_date / index_date / stock_date discriminator columns + 3 FKs to
--  stats.{etf,index,stock}_identity ON DELETE CASCADE + 3 partial
--  FK-support indexes per table — plus sec_type CHECK constraints;
--  removed to keep the schema simple and bulk inserts cheap.)
--  Consequence: deleting/truncating stats identity rows no longer
--  cascades into these tables — rebuild them with
--  `python -m analyze.mov_ave_spread` (or `--force`) instead.
-- ============================================================================

-- Drop any prior version so a re-run rebuilds the table from the CREATE
-- below (data is repopulated by the pipeline --force runs). Also remove
-- stale analysis_identity rows: 'etf_mov_ave_spread' (the retired
-- ETF-only predecessor) and 'mov_ave_rebounds' (a retired sibling
-- analysis whose registration row outlived its table).
DROP TABLE IF EXISTS analysis.mov_ave_spreads_detail CASCADE;
DELETE FROM analysis.analysis_identity WHERE name = 'etf_mov_ave_spread';
DELETE FROM analysis.analysis_identity WHERE name = 'mov_ave_rebounds';

-- ----------------------------------------------------------------------------
--  Table: analysis.mov_ave_spreads_detail  (WIDE format)
--    One row per (sec_type, code, date) holding all 9 gap values plus the
--    20 trading-amount liquidity-trend columns.
--
--  Columns:
--    sec_type, code, date              — PK; identifies the asset universe
--                                            (etf vs index), ticker, and date
--    trading_amt_ma{5,20,60,120,255}     — trading-amount MAs + market-share
--    trading_amt_market_share_ma{...}      MAs + fractional MA slopes +
--    trading_amt_ma{...}_slope             market-share-vs-MA gaps (20 cols,
--    trading_amt_market_share_vs_ma{...}   mirrored into mov_ave_trading_amt;
--                                            see that section for semantics)
--    price_vs_ma{5,20,60,120,255}        — (price - maX) / maX
--    ma5_vs_ma{20,60,120,255}            — (ma5  - maX) / maX
--    std_{5,20,60,120,255}days            — rolling population σ of price over
--                                            N trading days (Bollinger band
--                                            width). Same units as price.
--                                            NULL until N rows are available.
--
--  Gap values are stored as signed fractional ratios (e.g. 0.05 = +5%).
--  NULL when either the numerator or denominator is NULL or non-positive.
--  σ values are in price units (not price²) so NUMERIC(10,6) holds them
--  without overflow for any realistic ETF / index / stock price.
-- ----------------------------------------------------------------------------
CREATE TABLE analysis.mov_ave_spreads_detail (
    sec_type        TEXT         NOT NULL,  -- 'etf' | 'index' | 'stock'
    code              TEXT         NOT NULL,
    date              DATE         NOT NULL,

    -- 20 trading-amount liquidity-trend columns (mirrored into
    -- analysis.mov_ave_trading_amt by the trading-amt internal step; kept
    -- here too so single-table chart queries need no JOIN). Definitions
    -- and semantics: see the mov_ave_trading_amt section below.
    trading_amt_ma5                   NUMERIC(24,4),
    trading_amt_ma20                  NUMERIC(24,4),
    trading_amt_ma60                  NUMERIC(24,4),
    trading_amt_ma120                 NUMERIC(24,4),
    trading_amt_ma255                 NUMERIC(24,4),
    trading_amt_market_share_ma5      NUMERIC(10,4),
    trading_amt_market_share_ma20     NUMERIC(10,4),
    trading_amt_market_share_ma60     NUMERIC(10,4),
    trading_amt_market_share_ma120    NUMERIC(10,4),
    trading_amt_market_share_ma255    NUMERIC(10,4),
    trading_amt_ma5_slope             NUMERIC(10,4),
    trading_amt_ma20_slope            NUMERIC(10,4),
    trading_amt_ma60_slope            NUMERIC(10,4),
    trading_amt_ma120_slope           NUMERIC(10,4),
    trading_amt_ma255_slope           NUMERIC(10,4),
    trading_amt_market_share_vs_ma5   NUMERIC(10,4),
    trading_amt_market_share_vs_ma20  NUMERIC(10,4),
    trading_amt_market_share_vs_ma60  NUMERIC(10,4),
    trading_amt_market_share_vs_ma120 NUMERIC(10,4),
    trading_amt_market_share_vs_ma255 NUMERIC(10,4),

    -- 5 Price-vs-MA gap columns
    price_vs_ma5      NUMERIC(10,6),
    price_vs_ma20     NUMERIC(10,6),
    price_vs_ma60     NUMERIC(10,6),
    price_vs_ma120    NUMERIC(10,6),
    price_vs_ma255    NUMERIC(10,6),

    -- 4 MA5-vs-MA gap columns
    ma5_vs_ma20       NUMERIC(10,6),
    ma5_vs_ma60       NUMERIC(10,6),
    ma5_vs_ma120      NUMERIC(10,6),
    ma5_vs_ma255      NUMERIC(10,6),

    price_slope       NUMERIC(10,6), -- 1st derivative of price
    ma5_slope         NUMERIC(10,6), -- 1st derivative of MA5
    ma20_slope        NUMERIC(10,6), -- 1st derivative of MA20
    ma60_slope        NUMERIC(10,6), -- 1st derivative of MA60
    ma120_slope       NUMERIC(10,6), -- 1st derivative of MA120
    ma255_slope       NUMERIC(10,6),

    price_curvature   NUMERIC(10,6), -- 2nd derivative of price
    ma5_curvature     NUMERIC(10,6), -- 2nd derivative of MA5
    ma20_curvature    NUMERIC(10,6), -- 2nd derivative of MA20
    ma60_curvature    NUMERIC(10,6), -- 2nd derivative of MA60
    ma120_curvature   NUMERIC(10,6), -- 2nd derivative of MA120
    ma255_curvature   NUMERIC(10,6), -- 2nd derivative of MA255

    -- 5 rolling population σ columns (in price units, Bollinger band width).
    -- σ_N[t] = sqrt( mean( (price[t-N+1..t] - mean(price[t-N+1..t]))^2 ) )
    -- using ddof=0 (population std, the Bollinger convention). NULL until
    -- the rolling window is fully populated (N consecutive rows).
    std_5days         NUMERIC(10,6),
    std_20days        NUMERIC(10,6),
    std_60days        NUMERIC(10,6),
    std_120days       NUMERIC(10,6),
    std_255days       NUMERIC(10,6),

    CONSTRAINT pk_mov_ave_spreads_detail PRIMARY KEY (code, sec_type, date)
) PARTITION BY HASH (code);

-- Native hash partitions (16) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p15
SELECT public.create_hash_partitions('analysis', 'mov_ave_spreads_detail', 16);

-- NOTE: no separate (sec_type, code, date) index — the PK already covers
-- that lookup. A duplicate index was previously created here and dropped
-- because it doubled index-maintenance cost on every INSERT for zero
-- benefit (PK B-tree already serves equality + range scans on the
-- (sec_type, code, date) prefix).
--
--
COMMENT ON TABLE  analysis.mov_ave_spreads_detail              IS 'MA-spread detail (WIDE format): one row per (code, sec_type, date) with 9 gap_value columns (5 Price/MA + 4 MA5/MA). sec_type ∈ {etf, index, stock}.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.sec_type   IS 'Security type: etf (ETF), index (CSI-style index), or stock (individual equity).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.code         IS 'Ticker. ETFs use exchange suffix (e.g. "510050.SS"); indices use bare code (e.g. "000300").';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.date         IS 'Business date (trading day).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.trading_amt_ma5    IS '5-trading-day moving average of trading_amount (yuan) per (sec_type, code). NUMERIC(24,4) matches source precision (stats.{etf_liquidity_margin,index_basic_stats,stock_liquidity_margin}.trading_amount) so broad-index daily turnover up to 10^20 yuan fits without overflow. NULL until 5 rows. NULL trading_amount values are treated as 0 (zero turnover) in the rolling sum but still counted in the W-row denominator, so a single NULL date no longer creates a W-day NaN gap.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.trading_amt_ma20   IS '20-trading-day moving average of trading_amount (yuan). NULL until 20 rows. NULL trading_amount treated as 0 in sum, counted in denominator (see trading_amt_ma5).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.trading_amt_ma60   IS '60-trading-day moving average of trading_amount (yuan). NULL until 60 rows. NULL trading_amount treated as 0 in sum, counted in denominator (see trading_amt_ma5).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.trading_amt_ma120  IS '120-trading-day moving average of trading_amount (yuan). NULL until 120 rows. NULL trading_amount treated as 0 in sum, counted in denominator (see trading_amt_ma5).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.trading_amt_ma255  IS '255-trading-day moving average of trading_amount (yuan). NULL until 255 rows. NULL trading_amount treated as 0 in sum, counted in denominator (see trading_amt_ma5).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.trading_amt_market_share_ma5    IS '5-trading-day moving average of trading_amt_market_share (dimensionless ratio 0..1). market_share[date,code] = trading_amount[date,code] / denominator[date], where denominator = SUM(stats.exchange_trading_amt.total_trading_amount) across exchanges whose stats.sec_classification.is_primary_exchange = TRUE on that date. NULL until 5 rows. NULL market_share treated as 0 in rolling mean, counted in W-row denominator (same pattern as trading_amt_ma5). Built by analyze.mov_ave_spread.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.trading_amt_market_share_ma20   IS '20-trading-day moving average of trading_amt_market_share. NULL until 20 rows. NULL market_share treated as 0 in rolling mean, counted in denominator (see trading_amt_market_share_ma5).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.trading_amt_market_share_ma60   IS '60-trading-day moving average of trading_amt_market_share. NULL until 60 rows. NULL market_share treated as 0 in rolling mean, counted in denominator (see trading_amt_market_share_ma5).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.trading_amt_market_share_ma120  IS '120-trading-day moving average of trading_amt_market_share. NULL until 120 rows. NULL market_share treated as 0 in rolling mean, counted in denominator (see trading_amt_market_share_ma5).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.trading_amt_market_share_ma255  IS '255-trading-day moving average of trading_amt_market_share. NULL until 255 rows. NULL market_share treated as 0 in rolling mean, counted in denominator (see trading_amt_market_share_ma5).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.trading_amt_ma5_slope    IS 'Fractional daily change of trading_amt_ma5: (ma5[t] - ma5[t-1]) / ma5[t-1]. Signed ratio (e.g. 0.02 = +2% day-over-day change in the 5-day trading-amount MA). NULL on the first date of each code (no prior row) or when ma5[t]/ma5[t-1] is NULL or ma5[t-1] <= 0. NUMERIC(10,4) — ratio values are small (typical |slope| < 0.1). Built by analyze.mov_ave_spread.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.trading_amt_ma20_slope   IS 'Fractional daily change of trading_amt_ma20: (ma20[t] - ma20[t-1]) / ma20[t-1]. NULL on first date or when ma is NULL/<=0 (see trading_amt_ma5_slope).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.trading_amt_ma60_slope   IS 'Fractional daily change of trading_amt_ma60: (ma60[t] - ma60[t-1]) / ma60[t-1]. NULL on first date or when ma is NULL/<=0 (see trading_amt_ma5_slope).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.trading_amt_ma120_slope  IS 'Fractional daily change of trading_amt_ma120: (ma120[t] - ma120[t-1]) / ma120[t-1]. NULL on first date or when ma is NULL/<=0 (see trading_amt_ma5_slope).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.trading_amt_ma255_slope  IS 'Fractional daily change of trading_amt_ma255: (ma255[t] - ma255[t-1]) / ma255[t-1]. NULL on first date or when ma is NULL/<=0 (see trading_amt_ma5_slope).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.trading_amt_market_share_vs_ma5    IS 'Signed fractional gap between current market_share and its 5-day MA: (market_share - trading_amt_market_share_ma5) / trading_amt_market_share_ma5. market_share[date,code] = trading_amount[date,code] / denominator[date], where denominator = SUM(stats.exchange_trading_amt.total_trading_amount) across exchanges whose stats.sec_classification.is_primary_exchange = TRUE on that date. Positive = security is gaining relative liquidity (above its 5-day average market share); negative = losing. NULL when market_share or market_share_ma5 is NULL or market_share_ma5 <= 0. Built by analyze.mov_ave_spread.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.trading_amt_market_share_vs_ma20   IS 'Signed fractional gap between current market_share and its 20-day MA: (market_share - trading_amt_market_share_ma20) / trading_amt_market_share_ma20. NULL when market_share or market_share_ma20 is NULL or <= 0 (see trading_amt_market_share_vs_ma5).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.trading_amt_market_share_vs_ma60   IS 'Signed fractional gap between current market_share and its 60-day MA: (market_share - trading_amt_market_share_ma60) / trading_amt_market_share_ma60. NULL when market_share or market_share_ma60 is NULL or <= 0 (see trading_amt_market_share_vs_ma5).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.trading_amt_market_share_vs_ma120  IS 'Signed fractional gap between current market_share and its 120-day MA: (market_share - trading_amt_market_share_ma120) / trading_amt_market_share_ma120. NULL when market_share or market_share_ma120 is NULL or <= 0 (see trading_amt_market_share_vs_ma5).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.trading_amt_market_share_vs_ma255  IS 'Signed fractional gap between current market_share and its 255-day MA: (market_share - trading_amt_market_share_ma255) / trading_amt_market_share_ma255. NULL when market_share or market_share_ma255 is NULL or <= 0 (see trading_amt_market_share_vs_ma5).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.price_vs_ma5 IS '(price - ma5) / ma5 — signed fractional gap (NULL when either is NULL/invalid).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.price_vs_ma20 IS '(price - ma20) / ma20.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.price_vs_ma60 IS '(price - ma60) / ma60.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.price_vs_ma120 IS '(price - ma120) / ma120.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.price_vs_ma255 IS '(price - ma255) / ma255.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.ma5_vs_ma20  IS '(ma5 - ma20) / ma20.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.ma5_vs_ma60  IS '(ma5 - ma60) / ma60.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.ma5_vs_ma120 IS '(ma5 - ma120) / ma120.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.ma5_vs_ma255 IS '(ma5 - ma255) / ma255.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.price_slope     IS '1st derivative of price (price[t] - price[t-1]) per trading day. NULL on the first date of each code.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.ma5_slope       IS '1st derivative of MA5 (MA5[t] - MA5[t-1]) per trading day. NULL on the first date of each code.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.ma20_slope      IS '1st derivative of MA20 (MA20[t] - MA20[t-1]).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.ma60_slope      IS '1st derivative of MA60 (MA60[t] - MA60[t-1]).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.ma120_slope     IS '1st derivative of MA120 (MA120[t] - MA120[t-1]).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.ma255_slope     IS '1st derivative of MA255 (MA255[t] - MA255[t-1]).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.price_curvature IS '2nd derivative of price (slope[t] - slope[t-1]). NULL on the first two dates of each code.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.ma5_curvature   IS '2nd derivative of MA5 (slope[t] - slope[t-1]). NULL on the first two dates of each code.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.ma20_curvature  IS '2nd derivative of MA20 (slope[t] - slope[t-1]).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.ma60_curvature  IS '2nd derivative of MA60 (slope[t] - slope[t-1]).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.ma120_curvature IS '2nd derivative of MA120 (slope[t] - slope[t-1]).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.ma255_curvature IS '2nd derivative of MA255 (slope[t] - slope[t-1]).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.std_5days   IS 'Rolling population σ (ddof=0) of price over 5 trading days. Bollinger band width for MA5 envelope. NULL until 5 consecutive rows.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.std_20days  IS 'Rolling population σ (ddof=0) of price over 20 trading days. Bollinger band width for MA20 envelope. NULL until 20 consecutive rows.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.std_60days  IS 'Rolling population σ (ddof=0) of price over 60 trading days. Bollinger band width for MA60 envelope. NULL until 60 consecutive rows.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.std_120days IS 'Rolling population σ (ddof=0) of price over 120 trading days. Bollinger band width for MA120 envelope. NULL until 120 consecutive rows.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.std_255days IS 'Rolling population σ (ddof=0) of price over 255 trading days. Bollinger band width for MA255 envelope. NULL until 255 consecutive rows.';
