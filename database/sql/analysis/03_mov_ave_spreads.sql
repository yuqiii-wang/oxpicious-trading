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

-- Drop any prior version of these tables (ETF-only etf_mov_ave_spreads_* and
-- earlier wide-format revisions). Also remove the old analysis_identity row
-- so the new mov_ave_spread registration starts clean.
DROP TABLE IF EXISTS analysis.mov_ave_spreads_detail CASCADE;
DROP TABLE IF EXISTS analysis.mov_ave_spreads_detail_ema CASCADE;
DROP TABLE IF EXISTS analysis.mov_ave_spreads_detail_ohlc CASCADE;
DROP TABLE IF EXISTS analysis.mov_ave_large_swings CASCADE;
DROP TABLE IF EXISTS analysis.mov_ave_rsi CASCADE;
DROP TABLE IF EXISTS analysis.etf_mov_ave_spreads_detail;
DROP TABLE IF EXISTS analysis.mov_ave_peaks_and_floors CASCADE;
DROP TABLE IF EXISTS analysis.mov_ave_rebounds CASCADE;
DELETE FROM analysis.analysis_identity WHERE name = 'etf_mov_ave_spread';
DELETE FROM analysis.analysis_identity WHERE name = 'mov_ave_rsi';
DELETE FROM analysis.analysis_identity WHERE name = 'mov_ave_spread_ema';
DELETE FROM analysis.analysis_identity WHERE name = 'mov_ave_spread_ohlc';
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

-- ----------------------------------------------------------------------------
--  Table: analysis.mov_ave_spreads_detail_ema  (WIDE: one row per
--  asset+code+date, 9 EMA gap columns + 5 EMA slope + 5 EMA curvature)
--
--  EMA counterpart of mov_ave_spreads_detail. Source: stats.{etf,index,
--  stock}_tech_stats.ema{6,20,60,120,255} (already fetched by the parent
--  mov_ave_spread pipeline — reuses the same source DataFrame, no second
--  DB round-trip).
--
--  9 gap pairs (canonical order):
--    5 Price-vs-EMA pairs:  gap = (price - emaX) / emaX,  X ∈ {6,20,60,120,255}
--    4 EMA6-vs-EMA pairs:   gap = (ema6 - emaX) / emaX,   X ∈ {20,60,120,255}
--
--  5 EMA slope columns (1st derivative = group-diff per (sec_type, code)
--  ordered by date) + 5 EMA curvature columns (2nd derivative = diff of
--  slope). NULL on first date (slope) / first two dates (curvature) of
--  each code.
--
--  Populated by the internal EMA step of `analyze.mov_ave_spread` (see
--  ema.py). Incremental upsert by missing dates; --force truncates first.
-- ----------------------------------------------------------------------------
CREATE TABLE analysis.mov_ave_spreads_detail_ema (
    sec_type          TEXT         NOT NULL,  -- 'etf' | 'index' | 'stock'
    code              TEXT         NOT NULL,
    date              DATE         NOT NULL,

    -- 5 Price-vs-EMA gap columns
    price_vs_ema6     NUMERIC(10,6),
    price_vs_ema20    NUMERIC(10,6),
    price_vs_ema60    NUMERIC(10,6),
    price_vs_ema120   NUMERIC(10,6),
    price_vs_ema255   NUMERIC(10,6),

    -- 4 EMA6-vs-EMA gap columns
    ema6_vs_ema20     NUMERIC(10,6),
    ema6_vs_ema60     NUMERIC(10,6),
    ema6_vs_ema120    NUMERIC(10,6),
    ema6_vs_ema255    NUMERIC(10,6),

    -- 5 EMA slope columns (1st derivative per (sec_type, code) by date)
    ema6_slope        NUMERIC(10,6),
    ema20_slope       NUMERIC(10,6),
    ema60_slope       NUMERIC(10,6),
    ema120_slope      NUMERIC(10,6),
    ema255_slope      NUMERIC(10,6),

    -- 5 EMA curvature columns (2nd derivative = diff of slope)
    ema6_curvature    NUMERIC(10,6),
    ema20_curvature   NUMERIC(10,6),
    ema60_curvature   NUMERIC(10,6),
    ema120_curvature  NUMERIC(10,6),
    ema255_curvature  NUMERIC(10,6),

    std_5days         NUMERIC(10,6),
    std_20days        NUMERIC(10,6),
    std_60days        NUMERIC(10,6),
    std_120days       NUMERIC(10,6),
    std_255days       NUMERIC(10,6),

    CONSTRAINT pk_mov_ave_spreads_detail_ema PRIMARY KEY (code, sec_type, date)
) PARTITION BY HASH (code);

-- Native hash partitions (16) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p15
SELECT public.create_hash_partitions('analysis', 'mov_ave_spreads_detail_ema', 16);

COMMENT ON TABLE  analysis.mov_ave_spreads_detail_ema              IS 'EMA-spread detail (WIDE format): one row per (code, sec_type, date) with 9 EMA gap columns (5 Price/EMA + 4 EMA6/EMA) + 5 EMA slope + 5 EMA curvature columns. sec_type ∈ {etf, index, stock}. Source: stats.{etf,index,stock}_tech_stats.ema{6,20,60,120,255}.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ema.sec_type     IS 'Security type: etf (ETF), index (CSI-style index), or stock (individual equity).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ema.code         IS 'Ticker. ETFs use exchange suffix (e.g. "510050.SS"); indices use bare code (e.g. "000300").';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ema.date         IS 'Business date (trading day).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ema.price_vs_ema6   IS '(price - ema6) / ema6 — signed fractional gap (NULL when either is NULL/invalid).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ema.price_vs_ema20  IS '(price - ema20) / ema20.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ema.price_vs_ema60  IS '(price - ema60) / ema60.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ema.price_vs_ema120 IS '(price - ema120) / ema120.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ema.price_vs_ema255 IS '(price - ema255) / ema255.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ema.ema6_vs_ema20  IS '(ema6 - ema20) / ema20.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ema.ema6_vs_ema60  IS '(ema6 - ema60) / ema60.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ema.ema6_vs_ema120 IS '(ema6 - ema120) / ema120.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ema.ema6_vs_ema255 IS '(ema6 - ema255) / ema255.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ema.ema6_slope        IS '1st derivative of EMA6 (EMA6[t] - EMA6[t-1]) per trading day. NULL on the first date of each code.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ema.ema20_slope       IS '1st derivative of EMA20 (EMA20[t] - EMA20[t-1]).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ema.ema60_slope       IS '1st derivative of EMA60 (EMA60[t] - EMA60[t-1]).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ema.ema120_slope      IS '1st derivative of EMA120 (EMA120[t] - EMA120[t-1]).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ema.ema255_slope      IS '1st derivative of EMA255 (EMA255[t] - EMA255[t-1]).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ema.ema6_curvature    IS '2nd derivative of EMA6 (slope[t] - slope[t-1]). NULL on the first two dates of each code.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ema.ema20_curvature   IS '2nd derivative of EMA20 (slope[t] - slope[t-1]).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ema.ema60_curvature   IS '2nd derivative of EMA60 (slope[t] - slope[t-1]).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ema.ema120_curvature  IS '2nd derivative of EMA120 (slope[t] - slope[t-1]).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ema.ema255_curvature  IS '2nd derivative of EMA255 (slope[t] - slope[t-1]).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ema.std_5days   IS 'Rolling population σ (ddof=0) of price over 5 trading days. Bollinger band width for the EMA6 envelope (EMA6 uses the 5-day σ as the closest available window). NULL until 5 consecutive rows. Same source data as analysis.mov_ave_spreads_detail.std_5days — populated from the parent pipeline so the EMA table is self-contained for Bollinger rendering without a JOIN back to the SMA detail table.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ema.std_20days  IS 'Rolling population σ (ddof=0) of price over 20 trading days. Bollinger band width for the EMA20 envelope. NULL until 20 consecutive rows. Same source data as analysis.mov_ave_spreads_detail.std_20days.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ema.std_60days  IS 'Rolling population σ (ddof=0) of price over 60 trading days. Bollinger band width for the EMA60 envelope. NULL until 60 consecutive rows. Same source data as analysis.mov_ave_spreads_detail.std_60days.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ema.std_120days IS 'Rolling population σ (ddof=0) of price over 120 trading days. Bollinger band width for the EMA120 envelope. NULL until 120 consecutive rows. Same source data as analysis.mov_ave_spreads_detail.std_120days.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ema.std_255days IS 'Rolling population σ (ddof=0) of price over 255 trading days. Bollinger band width for the EMA255 envelope. NULL until 255 consecutive rows. Same source data as analysis.mov_ave_spreads_detail.std_255days.';

-- Migrate: add std_*days columns to pre-existing installs (CREATE TABLE
-- includes them for fresh installs, but ADD COLUMN IF NOT EXISTS retro-fits
-- the columns to an already-existing table without dropping data). Rolling
-- population σ (ddof=0) of price over N trading days — Bollinger band widths
-- for the EMA{W} envelopes. Same source data as the SMA detail table's
-- std_*days (σ of price over W days), populated from the parent pipeline's
-- compute_rolling_stds so the EMA table is self-contained for Bollinger
-- rendering. Built by analyze.mov_ave_spread (ema.py).
ALTER TABLE analysis.mov_ave_spreads_detail_ema ADD COLUMN IF NOT EXISTS std_5days   NUMERIC(10,6);
ALTER TABLE analysis.mov_ave_spreads_detail_ema ADD COLUMN IF NOT EXISTS std_20days  NUMERIC(10,6);
ALTER TABLE analysis.mov_ave_spreads_detail_ema ADD COLUMN IF NOT EXISTS std_60days  NUMERIC(10,6);
ALTER TABLE analysis.mov_ave_spreads_detail_ema ADD COLUMN IF NOT EXISTS std_120days NUMERIC(10,6);
ALTER TABLE analysis.mov_ave_spreads_detail_ema ADD COLUMN IF NOT EXISTS std_255days NUMERIC(10,6);

-- ----------------------------------------------------------------------------
--  Table: analysis.mov_ave_spreads_detail_ohlc  (OHLC summary per period,
--  LONG format)
--    One row per (sec_type, code, date, period): today_close plus rolling
--    OHLC stats for that period. period ∈ {20, 60, 120, 255, 500, 750,
--    1275} trading days. The per-window column names collapse into one
--    generic *_over_period column set — no duplicated names per window.
--
--  Columns:
--    sec_type, code, date, period       — PK; identifies the asset universe
--                                         (etf vs index), ticker, date, and
--                                         the rolling-window size
--    today_close                        — close price on `date`
--    open_over_period      — open price on the period-th trading day
--                            before `date`
--    high_over_period      — top-high anchor: MAXIMUM valid CLOSE in the
--                            1st HALF of the window (value = that anchor
--                            date's close)
--    low_over_period       — lowest-low anchor: MINIMUM valid CLOSE in the
--                            1st half (value = that anchor date's close)
--    high_2nd_over_period  — second-high anchor: MAXIMUM valid CLOSE in the
--                            2nd HALF of the window (2nd date is always
--                            later than the top date — the halves are
--                            disjoint and ordered, so the roof line runs
--                            forward in time)
--                            (value = that anchor date's INTRADAY HIGH)
--    low_2nd_over_period   — second-low anchor: MINIMUM valid CLOSE in the
--                            2nd half (2nd date is always later than the
--                            top date — the floor line runs forward in
--                            time)
--                            (value = that anchor date's INTRADAY LOW)
--
--  HALF-SPLIT ANCHORS: the window [date-period+1, date] is cut in half —
--  h = L // 2 with L the window length in trading-day positions (for odd
--  L the 2nd half gets the extra day); the 1st extreme is the max/min
--  valid CLOSE of the 1st half and the 2nd extreme the max/min valid
--  CLOSE of the 2nd half. Ties go to the earliest date; NaN closes are
--  skipped. Columns are NULL when the window has fewer than 2 positions
--  or the half holds no valid close.
--  Populated by the internal OHLC step of `analyze.mov_ave_spread`
--  (see ohlc.py). Reuses the same source DataFrame as the parent
--  pipeline — no second DB round-trip.
-- ----------------------------------------------------------------------------
CREATE TABLE analysis.mov_ave_spreads_detail_ohlc (
    sec_type          TEXT         NOT NULL,  -- 'etf' | 'index' | 'stock'
    code              TEXT         NOT NULL,
    date              DATE         NOT NULL,

    today_close       NUMERIC(18,6) NOT NULL,
    period            INTEGER      NOT NULL,  -- window size in trading days

    open_over_period          NUMERIC(18,6),
    high_over_period          NUMERIC(18,6),
    high_date_over_period    DATE,
    low_over_period           NUMERIC(18,6),
    low_date_over_period     DATE,
    high_2nd_over_period          NUMERIC(18,6),
    high_2nd_date_over_period     DATE,
    low_2nd_over_period           NUMERIC(18,6),
    low_2nd_date_over_period     DATE,
    high_line_slope_over_period NUMERIC(18,6),
    low_line_slope_over_period  NUMERIC(18,6),

    
    CONSTRAINT pk_mov_ave_spreads_detail_ohlc
        PRIMARY KEY (code, sec_type, date, period)
) PARTITION BY HASH (code);

-- Native hash partitions (32) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p31
SELECT public.create_hash_partitions('analysis', 'mov_ave_spreads_detail_ohlc', 32);

-- LONG format: one row per (code, sec_type, date, period). The per-window
-- columns collapse into a single generic *_over_period column set keyed by
-- `period` ∈ {20, 60, 120, 255, 500, 750, 1275} trading days.
COMMENT ON TABLE  analysis.mov_ave_spreads_detail_ohlc              IS 'OHLC detail (LONG format): one row per (code, sec_type, date, period) with today_close + rolling-window anchors per period ∈ {20,60,120,255,500,750,1275} trading days. HALF-SPLIT ANCHORS: the period window [date-period+1, date] is cut in half — h = L // 2 in trading-day positions (for odd L the 2nd half gets the extra day). Top anchors (high_over_period/low_over_period): the MAXIMUM/MINIMUM valid CLOSE of the 1st half (ties -> earliest date; value = anchor date close). 2nd anchors (high_2nd_over_period/low_2nd_over_period): the MAXIMUM/MINIMUM valid CLOSE of the 2nd half (value = anchor date INTRADAY high/low). The halves are disjoint and ordered, so the 2nd anchor date is ALWAYS strictly after the 1st anchor date wherever both exist. NaN closes are skipped; a half with no valid close NULLs its anchor, and windows with fewer than 2 positions have no anchors. DATE columns record the anchor dates. sec_type ∈ {etf, index, stock}. Source: same DataFrame as mov_ave_spread parent pipeline (no second DB round-trip).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ohlc.sec_type     IS 'Security type: etf (ETF), index (CSI-style index), or stock (individual equity).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ohlc.code        IS 'Ticker. ETFs use exchange suffix (e.g. "510050.SS"); indices use bare code (e.g. "000300").';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ohlc.date        IS 'Business date (trading day).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ohlc.today_close IS 'Close price on `date` (COALESCE(adj_close, close) for ETFs; close for index/stock). NOT NULL.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ohlc.period      IS 'Rolling-window size in trading days: one of {20, 60, 120, 255, 500, 750, 1275}. Part of the PK — the long format stores one row per (code, sec_type, date, period).';

COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ohlc.open_over_period   IS 'Open price on the period-th trading day before `date`. NULL if fewer than `period` prior rows exist.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ohlc.high_over_period   IS 'Top-high anchor: the MAXIMUM valid CLOSE in the 1st HALF of the period window ([date-period+1, date] cut in half, h = L // 2; ties -> earliest date); value = the anchor date close. NULL when the half holds no valid close.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ohlc.high_date_over_period IS 'Business date of the top-high anchor (max valid CLOSE of the window''s 1st half). NULL when the half holds no valid close.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ohlc.low_over_period    IS 'Lowest-low anchor: the MINIMUM valid CLOSE in the 1st HALF of the period window (ties -> earliest date); value = the anchor date close. NULL when the half holds no valid close.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ohlc.low_date_over_period IS 'Business date of the lowest-low anchor (min valid CLOSE of the window''s 1st half). NULL when the half holds no valid close.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ohlc.high_2nd_over_period IS 'Second-high anchor: the MAXIMUM valid CLOSE in the 2nd HALF of the period window (ties -> earliest date); value = the anchor date INTRADAY HIGH. The 2nd half is strictly after the 1st, so this date is always after the top-high anchor date. NULL when the half holds no valid close.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ohlc.high_2nd_date_over_period IS 'Business date of the second-high anchor (always strictly after the top-high anchor date). NULL when the half holds no valid close.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ohlc.low_2nd_over_period  IS 'Second-low anchor: the MINIMUM valid CLOSE in the 2nd HALF of the period window (ties -> earliest date); value = the anchor date INTRADAY LOW. The 2nd half is strictly after the 1st, so this date is always after the lowest-low anchor date. NULL when the half holds no valid close.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ohlc.low_2nd_date_over_period IS 'Business date of the second-low anchor (always strictly after the lowest-low anchor date). NULL when the half holds no valid close.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ohlc.high_line_slope_over_period IS 'Slope of the roof line through the two high anchors of this period (high_over_period close -> high_2nd_over_period intraday high), in price units per trading day: (high_2nd_over_period - high_over_period) / (trading days between the two anchor dates). Signed; the 2nd anchor always lies after the top in time. NULL when either anchor is absent.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ohlc.low_line_slope_over_period  IS 'Slope of the floor line through the two low anchors of this period (low_over_period close -> low_2nd_over_period intraday low), in price units per trading day. Signed; the 2nd anchor always lies after the bottom in time. NULL when either anchor is absent.';

-- ----------------------------------------------------------------------------
--  Table: analysis.mov_ave_rsi  (per-asset+date Wilder RSI + short-term gaps)
--    One row per (sec_type, code, date). Stores Wilder's Relative Strength
--    Index for 5 windows (6/10/14/20/60 days) and 2 short-term price-gap
--    (N-day return) columns (2/3 days).
--
--  RSI formula (Wilder's smoothing — EWM with alpha = 1/N, adjust=False):
--    delta[t]   = price[t] - price[t-1]                      (per code)
--    gain[t]    = max(delta, 0);  loss[t] = max(-delta, 0)
--    avg_gain   = gain.ewm(alpha=1/N, adjust=False, min_periods=N).mean()
--    avg_loss   = loss.ewm(alpha=1/N, adjust=False, min_periods=N).mean()
--    RS         = avg_gain / avg_loss
--    RSI        = 100 - 100 / (1 + RS)
--      RSI = 100  when avg_loss = 0 and avg_gain > 0  (pure uptrend)
--      RSI = 0    when avg_gain = 0 and avg_loss > 0  (pure downtrend)
--      RSI = NULL when avg_gain = 0 and avg_loss = 0  (flat / undefined)
--    NULL until N consecutive gain/loss observations are available
--    (min_periods=N). Range: 0..100.
--
--  Gap formula (N-day price return):
--    gap_Ndays[t] = (price[t] - price[t-N]) / price[t-N]   (per code)
--    NULL until N prior rows exist (first N rows per code are NULL).
--    Signed fractional ratio (e.g. 0.05 = +5%).
--
--  Source prices match mov_ave_spreads_detail: ETF uses
--  COALESCE(etf_adjustment.adj_close, etf_basic_stats.close); index uses
--  index_basic_stats.close; stock uses stock_basic_stats.close.
--
--  Populated by `analyze.mov_ave_spread` (internal RSI step in rsi.py;
--  incremental upsert by missing dates; --force truncates first). No FK to
--  mov_ave_spreads_detail — data integrity is guaranteed by INNER JOINs on
--  basic_stats in the build script.
-- ----------------------------------------------------------------------------
CREATE TABLE analysis.mov_ave_rsi (
    sec_type        TEXT         NOT NULL,  -- 'etf' | 'index' | 'stock'
    code            TEXT         NOT NULL,
    date            DATE         NOT NULL,

    -- Wilder RSI columns (0..100, NULL until N periods).
    -- Windows: 6 / 10 / 14 (classic Wilder) / 20 / 60 (~3 trading months).
    rsi_6days       NUMERIC(10,6),
    rsi_10days      NUMERIC(10,6),
    rsi_14days      NUMERIC(10,6),
    rsi_20days      NUMERIC(10,6),
    rsi_60days      NUMERIC(10,6),

    -- 2 short-term price-gap (N-day return) columns
    gap_2days       NUMERIC(10,6), -- sign indicates last extreme is max or min
    gap_3days       NUMERIC(10,6), -- sign indicates last extreme is max or min 
    gap_since_last_extreme_500days NUMERIC(10,6), -- sign indicates last extreme is max or min
    days_since_last_extreme_500days NUMERIC(10,6),
    date_of_last_extreme_500days DATE,

    CONSTRAINT pk_mov_ave_rsi PRIMARY KEY (code, sec_type, date)
) PARTITION BY HASH (code);

-- Native hash partitions (16) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p15
SELECT public.create_hash_partitions('analysis', 'mov_ave_rsi', 16);

-- Migrate: add rsi_60days column to pre-existing installs (CREATE TABLE
-- includes it for fresh installs, but ADD COLUMN IF NOT EXISTS retro-fits
-- the column to an already-existing table without dropping data). Wilder
-- RSI over 60 trading days (alpha=1/60, ewm adjust=False,
-- min_periods=60) — a longer-term momentum window complementing the
-- classic 14-day Wilder default. 0..100. NULL until 60 consecutive
-- gain/loss observations. Built by analyze.mov_ave_spread (rsi.py).
ALTER TABLE analysis.mov_ave_rsi ADD COLUMN IF NOT EXISTS rsi_60days NUMERIC(10,6);

-- Migrate: drop rsi_120days / rsi_255days / rsi_500days columns (removed
-- from the schema and the calculation — the analysis_forecasts mov_rsi
-- module only uses windows 6/10/14/20/60). Safe on fresh installs where
-- the columns never existed. Existing values are discarded; no recompute
-- needed since no consumer reads them.
ALTER TABLE analysis.mov_ave_rsi DROP COLUMN IF EXISTS rsi_120days;
ALTER TABLE analysis.mov_ave_rsi DROP COLUMN IF EXISTS rsi_255days;
ALTER TABLE analysis.mov_ave_rsi DROP COLUMN IF EXISTS rsi_500days;

-- Migrate: replace the unbounded last-extreme columns (gap_since_last_extreme
-- / days_since_last_extreme / date_of_last_extreme — "most recent turning
-- point ever") with 500-trading-day bounded variants. The _500days columns
-- only look at turning points within the last 500 trading days of the code's
-- history: NULL when the most recent extreme is older than 500 trading days
-- (or no extreme exists yet). SEMANTICS CHANGE — the old unbounded columns
-- are dropped and existing values must be recomputed by re-running
-- analyze.mov_ave_spread in force mode (rsi.py step truncates mov_ave_rsi).
ALTER TABLE analysis.mov_ave_rsi ADD COLUMN IF NOT EXISTS gap_since_last_extreme_500days NUMERIC(10,6);
ALTER TABLE analysis.mov_ave_rsi ADD COLUMN IF NOT EXISTS days_since_last_extreme_500days NUMERIC(10,6);
ALTER TABLE analysis.mov_ave_rsi ADD COLUMN IF NOT EXISTS date_of_last_extreme_500days DATE;
ALTER TABLE analysis.mov_ave_rsi DROP COLUMN IF EXISTS gap_since_last_extreme;
ALTER TABLE analysis.mov_ave_rsi DROP COLUMN IF EXISTS days_since_last_extreme;
ALTER TABLE analysis.mov_ave_rsi DROP COLUMN IF EXISTS date_of_last_extreme;

-- NOTE: no separate (sec_type, code, date) index — the PK already covers
-- that lookup (same rationale as mov_ave_spreads_detail above). A duplicate
-- index was previously created here and dropped because it doubled index-
-- maintenance cost on every INSERT for zero benefit (PK B-tree already
-- serves equality + range scans on the (sec_type, code, date) prefix).

COMMENT ON TABLE  analysis.mov_ave_rsi             IS 'Wilder RSI (6/10/14/20/60 days) + short-term price gaps (2/3 day returns). One row per (code, sec_type, date). sec_type ∈ {etf, index, stock}. analysis.mov_ave_rsi_holiday FK-references this table ON DELETE CASCADE.';
COMMENT ON COLUMN analysis.mov_ave_rsi.sec_type    IS 'Security type: etf (ETF), index (CSI-style index), or stock (individual equity).';
COMMENT ON COLUMN analysis.mov_ave_rsi.code        IS 'Ticker. ETFs use exchange suffix (e.g. "510050.SS"); indices use bare code (e.g. "000300").';
COMMENT ON COLUMN analysis.mov_ave_rsi.date        IS 'Business date (trading day).';
COMMENT ON COLUMN analysis.mov_ave_rsi.rsi_6days   IS 'Wilder RSI over 6 trading days (alpha=1/6, ewm adjust=False, min_periods=6). 0..100. NULL until 6 consecutive gain/loss observations.';
COMMENT ON COLUMN analysis.mov_ave_rsi.rsi_10days  IS 'Wilder RSI over 10 trading days (alpha=1/10, ewm adjust=False, min_periods=10). 0..100. NULL until 10 periods.';
COMMENT ON COLUMN analysis.mov_ave_rsi.rsi_14days  IS 'Wilder RSI over 14 trading days (alpha=1/14, ewm adjust=False, min_periods=14) — the classic Wilder window. 0..100. NULL until 14 periods.';
COMMENT ON COLUMN analysis.mov_ave_rsi.rsi_20days  IS 'Wilder RSI over 20 trading days (alpha=1/20, ewm adjust=False, min_periods=20). 0..100. NULL until 20 periods.';
COMMENT ON COLUMN analysis.mov_ave_rsi.rsi_60days  IS 'Wilder RSI over 60 trading days (alpha=1/60, ewm adjust=False, min_periods=60) — a longer-term momentum window complementing the classic 14-day Wilder default. 0..100. NULL until 60 consecutive gain/loss observations. Computed by analyze.mov_ave_spread (rsi.py) using the same Wilder EWM recurrence as the other RSI windows (delta = price[t]-price[t-1] is cuDF-accelerated via grouped_diff; the per-window EWM stays on pandas because cuDF lacks grouped-ewm support — see rsi.py for the documented rationale).';
COMMENT ON COLUMN analysis.mov_ave_rsi.gap_2days   IS '2-day price return: (price[t] - price[t-2]) / price[t-2]. Signed fractional ratio. NULL for the first 2 rows of each code.';
COMMENT ON COLUMN analysis.mov_ave_rsi.gap_3days   IS '3-day price return: (price[t] - price[t-3]) / price[t-3]. Signed fractional ratio. NULL for the first 3 rows of each code.';
COMMENT ON COLUMN analysis.mov_ave_rsi.gap_since_last_extreme_500days IS 'Signed fractional gap from the most recent local turning point (high/low) detected by price_slope sign change WITHIN the last 500 trading days of the code history: (price[t] - extreme_price) / extreme_price. Sign indicates the type of the last extreme: positive = last extreme was a local MIN (price rebounded upward since the trough), negative = last extreme was a local MAX (price fell since the peak). NULL when no turning point exists in the 500-trading-day lookback window (early history before the first turn, or the most recent extreme is older than 500 trading days).';
COMMENT ON COLUMN analysis.mov_ave_rsi.days_since_last_extreme_500days IS 'Trading days since the most recent local turning point (high/low) detected by price_slope sign change within the last 500 trading days. 0 on the extreme row itself. NULL when no turning point exists in the 500-trading-day lookback window (or the most recent extreme is older than 500 trading days).';
COMMENT ON COLUMN analysis.mov_ave_rsi.date_of_last_extreme_500days IS 'The biz date of the most recent local turning point (high/low) detected by price_slope sign change within the last 500 trading days — i.e. the date on which the extreme_price referenced by gap_since_last_extreme_500days was observed. Carried forward (forward-filled) from each turning point until the next one. NULL when no turning point exists in the 500-trading-day lookback window (early history before the first turn, or the most recent extreme is older than 500 trading days).';


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
DROP TABLE IF EXISTS analysis.mov_ave_trading_amt_ext CASCADE;
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

-- ----------------------------------------------------------------------------
--  Table: analysis.mov_ave_trading_amt_ratios  (liquidity-impact ratios)
--    One row per (sec_type, code, date). Capital-per-movement ratios:
--    how many MILLIONS of yuan of trading amount accompany one unit of
--    price movement. This is the reciprocal of the Amihud (2002)
--    illiquidity measure (ILLIQ = |price change| / dollar volume): a
--    HIGH value means a deep market (much capital absorbed per unit of
--    movement); a LOW value means a thin market (small capital moved
--    price a lot).
--
--  Financial semantics — the daily price move decomposes into three
--  measurable legs, and each leg gets its own capital ratio:
--
--    close[t] - close[t-1]  (net daily move)      = price_slope
--    open[t]  - close[t-1]  (overnight gap)       — jump between sessions
--    high[t]  - low[t]     (intraday range)       — session movement envelope
--
--  1. Slope ratios (signed, close-to-close basis), NUMERIC(10,4):
--     trading_amt_vs_price_slope_ratio
--         = (trading_amount / 1M) / price_slope
--         today's capital per unit of NET daily price change.
--     trading_amt_ma{W}_vs_price_ma{W}_slope_ratio
--         = (trading_amt_ma{W} / 1M) / ma{W}_slope
--         matching-timescale: W-day average capital per unit of the W-day
--         price-MA daily step (trend liquidity — the capital behind the
--         average trend step, smoothed).
--
--  2. Range ratio (unsigned — intraday depth gauge), NUMERIC(10,4):
--     trading_amt_vs_high_low_ratio
--         = (trading_amount / 1M) / (high - low)
--         capital per unit of intraday range. Range-based liquidity in the
--         Parkinson-volatility spirit: high turnover + narrow range = deep
--         book; low turnover + wide range = volatile / thin session.
--
--  3. Overnight-gap ratio (signed — gap-day liquidity), NUMERIC(10,4):
--     trading_amt_vs_overnight_gap_ratio
--         = (trading_amount / 1M) / (open[t] - close[t-1])
--         capital traded on a day that gapped, per unit of gap. NOTE: the
--         draft name "gap betw prev close today close" would be literally
--         identical to price_slope (close[t] - close[t-1]); it is
--         implemented as the standard trading "gap" instead — where
--         today's session OPENS relative to yesterday's close — which is
--         the distinct, non-redundant quantity.
--
--  4. MA5 versions of 2 & 3 (matching timescale), NUMERIC(10,4):
--     trading_amt_ma5_vs_high_low_ma5_ratio
--         = (trading_amt_ma5 / 1M) / MA5(high - low)
--     trading_amt_ma5_vs_overnight_gap_ma5_ratio
--         = (trading_amt_ma5 / 1M) / MA5(open[t] - close[t-1])
--         5-day average capital per unit of 5-day average range / gap.
--
--  Sign convention: turnover >= 0, so sign(ratio) = sign(movement).
--  Negative slope/gap ratios = the move was downward.
--
--  Zero-movement guard (all columns): a 0 denominator is auto-set to 1.0
--  (the stored value then equals the capital in millions — a pragmatic
--  floor for flat / limit-locked days rather than a true ratio).
--
--  Scale note: ratios are in price units (not scale-free), so absolute
--  values are comparable across time within a code, and cross-sectionally
--  only among similarly-priced instruments. Typical magnitudes: 10^2
--  (small stocks) .. 10^5 (broad indices / liquid ETFs); values with
--  |v| >= 10^6 (the NUMERIC(10,4) cap) are nulled by the build script.
--
--  Populated by the internal trading-amt-ratios step of
--  `analyze.mov_ave_spread` (see trading_amt_ratios.py). Incremental
--  upsert by missing dates; --force truncates first.
-- ----------------------------------------------------------------------------

CREATE TABLE analysis.mov_ave_trading_amt_ratios (
    sec_type        TEXT         NOT NULL,  -- 'etf' | 'index' | 'stock'
    code            TEXT         NOT NULL,
    date            DATE         NOT NULL,

    -- 6 slope ratios (signed, close-to-close basis; matching timescale)
    trading_amt_vs_price_slope_ratio             NUMERIC(10,4),
    trading_amt_ma5_vs_price_ma5_slope_ratio     NUMERIC(10,4),
    trading_amt_ma20_vs_price_ma20_slope_ratio   NUMERIC(10,4),
    trading_amt_ma60_vs_price_ma60_slope_ratio   NUMERIC(10,4),
    trading_amt_ma120_vs_price_ma120_slope_ratio NUMERIC(10,4),
    trading_amt_ma255_vs_price_ma255_slope_ratio NUMERIC(10,4),

    -- 4 range / overnight-gap ratios
    trading_amt_vs_high_low_ratio                NUMERIC(10,4),
    trading_amt_vs_overnight_gap_ratio           NUMERIC(10,4),
    trading_amt_ma5_vs_high_low_ma5_ratio        NUMERIC(10,4),
    trading_amt_ma5_vs_overnight_gap_ma5_ratio   NUMERIC(10,4),

    CONSTRAINT pk_mov_ave_trading_amt_ratios PRIMARY KEY (code, sec_type, date)
) PARTITION BY HASH (code);

-- Native hash partitions (16) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p15
SELECT public.create_hash_partitions('analysis', 'mov_ave_trading_amt_ratios', 16);

COMMENT ON TABLE  analysis.mov_ave_trading_amt_ratios              IS 'Liquidity-impact ratios (WIDE format): one row per (sec_type, code, date) with 10 capital-per-movement ratio columns — 6 slope ratios ((trading_amt or trading_amt_maW) / 1M yuan) / (price_slope or maW_slope), matching-timescale) + range ratio ((trading_amt / 1M) / (high - low)) + overnight-gap ratio ((trading_amt / 1M) / (open - prev close)) + MA5 versions of both. Reciprocal of the Amihud illiquidity measure: higher = deeper market. sec_type ∈ {etf, index, stock}. Built by analyze.mov_ave_spread (trading_amt_ratios.py).';
COMMENT ON COLUMN analysis.mov_ave_trading_amt_ratios.sec_type   IS 'Security type: etf (ETF), index (CSI-style index), or stock (individual equity).';
COMMENT ON COLUMN analysis.mov_ave_trading_amt_ratios.code         IS 'Ticker. ETFs use exchange suffix (e.g. "510050.SS"); indices use bare code (e.g. "000300").';
COMMENT ON COLUMN analysis.mov_ave_trading_amt_ratios.date         IS 'Business date (trading day).';
COMMENT ON COLUMN analysis.mov_ave_trading_amt_ratios.trading_amt_vs_price_slope_ratio       IS 'Liquidity-impact proxy: (trading_amount / 1,000,000) / price_slope — today''s capital (millions of yuan) per unit of NET close-to-close price change. Reciprocal of the Amihud illiquidity measure: higher = deeper market. Signed: negative = the daily move was downward. price_slope=0 auto-set to 1.0 (stored value = capital in millions). NULL when trading_amount or price_slope is NULL.';
COMMENT ON COLUMN analysis.mov_ave_trading_amt_ratios.trading_amt_ma5_vs_price_ma5_slope_ratio   IS '(trading_amt_ma5 / 1,000,000) / ma5_slope. Millions of capital (MA5 timescale) per unit of MA5 price change — trend liquidity. ma5_slope=0 auto-set to 1.0.';
COMMENT ON COLUMN analysis.mov_ave_trading_amt_ratios.trading_amt_ma20_vs_price_ma20_slope_ratio  IS '(trading_amt_ma20 / 1,000,000) / ma20_slope. Millions of capital (MA20 timescale) per unit of MA20 price change. ma20_slope=0 auto-set to 1.0.';
COMMENT ON COLUMN analysis.mov_ave_trading_amt_ratios.trading_amt_ma60_vs_price_ma60_slope_ratio  IS '(trading_amt_ma60 / 1,000,000) / ma60_slope. Millions of capital (MA60 timescale) per unit of MA60 price change. ma60_slope=0 auto-set to 1.0.';
COMMENT ON COLUMN analysis.mov_ave_trading_amt_ratios.trading_amt_ma120_vs_price_ma120_slope_ratio IS '(trading_amt_ma120 / 1,000,000) / ma120_slope. Millions of capital (MA120 timescale) per unit of MA120 price change. ma120_slope=0 auto-set to 1.0.';
COMMENT ON COLUMN analysis.mov_ave_trading_amt_ratios.trading_amt_ma255_vs_price_ma255_slope_ratio IS '(trading_amt_ma255 / 1,000,000) / ma255_slope. Millions of capital (MA255 timescale) per unit of MA255 price change. ma255_slope=0 auto-set to 1.0.';
COMMENT ON COLUMN analysis.mov_ave_trading_amt_ratios.trading_amt_vs_high_low_ratio                IS '(trading_amount / 1,000,000) / (high - low) — capital per unit of INTRADAY range (unsigned, always positive). Range-based liquidity: high turnover + narrow range = deep book (Parkinson-volatility spirit); low turnover + wide range = volatile / thin session. range=0 (limit-locked / flat day) auto-set denominator to 1.0 (stored value = capital in millions). NULL when trading_amount, high, or low is NULL.';
COMMENT ON COLUMN analysis.mov_ave_trading_amt_ratios.trading_amt_vs_overnight_gap_ratio           IS '(trading_amount / 1,000,000) / (open[t] - close[t-1]) — capital traded on a day that gapped, per unit of OVERNIGHT gap (where today''s session opens relative to yesterday''s close; the literal "prev close vs today close" reading would be identical to trading_amt_vs_price_slope_ratio). Signed: negative = gap down. Gap-day liquidity / confirmation gauge: high ratio on a big gap = the gap attracted flow; low ratio = thin overnight market. gap=0 auto-set denominator to 1.0. NULL on the first date per code or when trading_amount, open, or prev close is NULL.';
COMMENT ON COLUMN analysis.mov_ave_trading_amt_ratios.trading_amt_ma5_vs_high_low_ma5_ratio        IS '(trading_amt_ma5 / 1,000,000) / MA5(high - low) — matching timescale: 5-day average capital per unit of 5-day average daily range. NULL until 5 consecutive rows.';
COMMENT ON COLUMN analysis.mov_ave_trading_amt_ratios.trading_amt_ma5_vs_overnight_gap_ma5_ratio   IS '(trading_amt_ma5 / 1,000,000) / MA5(open[t] - close[t-1]) — matching timescale: 5-day average capital per unit of 5-day average overnight gap. NULL until 5 consecutive gap observations.';

-- ----------------------------------------------------------------------------
--  Table: analysis.mov_ave_market_hypes  (market-hype EPISODE detector)
--    One row per (sec_type, code, min_checkin_period, EPISODE): a
--    CONCATENATED hype episode — a maximal span of trading dates
--    anchored on a maximal run of CONSECUTIVE "hyped" dates (trading
--    amount AND price volatility BOTH elevated, SUSTAINEDLY over a
--    check-in window, each measured against its own CENTERED 20-year
--    percentile threshold) and EXTENDED through the surrounding
--    check-in evidence, bucketed BY ITS LENGTH: min_checkin_period is
--    the bucket's MINIMUM span and the NEXT window its EXCLUSIVE
--    maximum (5d: 5..19 rows; 20d: 20..59; 60d: 60..119; 120d:
--    120..254; 255d: 255..5100 = the whole ±10y threshold base).
--
--  Computation semantics (from the draft comment):
--
--  1. CENTERED PERCENTILE THRESHOLDS (per date t, per code — the audit
--     base window spans BOTH directions around the audited date, NOT a
--     trailing/rolling-back window):
--     trading_amt_threshold[t] = the min_trading_amt_threshold-th
--         PERCENTILE (0-100, linear interpolation) of daily
--         trading_amount over the centered window of ~2550 trading
--         rows (10 trading years) BEFORE t, t itself, and ~2550 rows
--         AFTER t (~5101 rows ≈ 20 trading years total).
--     std_threshold[t] = the min_std_threshold-th percentile of
--         std_{W}days over the same centered window, where W = the
--         row's min_checkin_period (matching timescale: the
--         volatility metric is the SAME W-day rolling population σ
--         already stored in mov_ave_spreads_detail.std_{W}days).
--     A base window with fewer than 255 non-NULL observations (1
--     trading year) has no thresholds -> the date is not hyped. Bases
--     near the start / end of a code's history are naturally truncated
--     (the newest dates have no future rows yet, so their base is
--     effectively the trailing 10y). Because the base looks both ways,
--     historical rows use their FOLLOWING decade (retrospective audit,
--     look-ahead by design) — run --force to refresh historical rows'
--     flags after new data arrives.
--
--  2. CHECK-IN CONDITION (per date s):
--     checkin[s] = trading_amount[s] > trading_amt_threshold[s]
--                  AND std_{W}days[s] > std_threshold[s]
--     Strict > on both legs (the draft's ">min_trading_amt_threshold
--     and min_std_threshold"). NULL turnover / σ counts as NOT a
--     check-in.
--
--  3. SATISFACTION (per row date t, "within min_checkin_period from
--     today date"):
--     Within the last W trading rows ending at t (inclusive), the
--     percentage of check-in dates must EXCEED
--     min_checkin_satisfaction_threshold (strict >; the 60.0 default
--     means "> 60% of the W days checked in"). The denominator is the
--     full W rows — missing data counts against satisfaction. The
--     first W-1 rows of each code have no full window -> not hyped.
--
--  4. EPISODE CONCAT + EXTENSION + BUCKETING (what the table stores):
--     The per-date hyped series from (3) is collapsed, per (sec_type,
--     code, min_checkin_period), into maximal runs of CONSECUTIVE
--     hyped dates ("cores"), then each core is extended through the
--     check-in evidence that fed its satisfaction and bucketed by its
--     SPAN:
--       - start: the FIRST check-in within the W rows ending at the
--         core's first hyped date (the lookback window that produced
--         the core's first satisfaction verdict — its earliest
--         evidence). This lets an episode start at the FIRST big-move
--         day of a turmoil instead of ~W rows later, when the trailing
--         satisfaction count finally crosses the threshold (the
--         2024-09-24 rally audit, 159673.SZ: the 20d satisfaction only
--         crossed 60% on 2024-10-21 — a full month late — while the
--         check-ins began on the rally's day 1).
--       - end: symmetric — the LAST check-in within the W rows
--         starting at the core's last hyped date (the decaying tail).
--       - episodes of one bucket never overlap; each start is clipped
--         to just after the previous episode's end.
--       - BUCKET BOUNDS: hype_days (the span in trading dates,
--         start and end inclusive) must satisfy
--         W <= hype_days < next check-in window (the longest window
--         is bounded by 5100 rows = the whole ±10y base). A core whose
--         own consecutive span already reaches its bucket max is
--         dropped from that bucket: sustained activity of that length
--         is the domain of the NEXT bucket up, whose own
--         longer-window satisfaction flags it.
--       - trading_amt_hype_days / std_hype_days count the days within
--         the stored span on which each leg individually checked in
--         (diagnostics for which leg drove the episode).
--     Only qualifying spans are stored — non-hyped dates leave no
--     footprint (the pre-episode revision wrote one is_hyped row per
--     date, TRUE and FALSE alike; superseded).
--
--  Row multiplicity: one row per EPISODE per check-in window
--  (5 / 20 / 60 / 120 / 255). min_checkin_period IS part of the PK —
--  different windows can produce episodes with identical spans, so
--  the window must disambiguate the rows. The three threshold columns
--  RECORD the parameter set the build used (defaults 60.0 / 60.0 /
--  30.0 — the σ leg sits at a DELIBERATELY LOW 30th percentile: the
--  W-day trailing σ lags a sudden turmoil by construction, so a low
--  bar lets episodes start at the turmoil's first big-move day); they
--  are NOT part of the PK — rebuilding with different parameters (use
--  --force) overwrites in place.
--
--  Rebuild semantics (same as analysis.margin_changes): episode
--  boundaries shift whenever new dates arrive (the trailing episode
--  extends; the centered threshold windows move), and non-hyped dates
--  leave no footprint — date-level coverage cannot be diffed against
--  an episodes table. The build therefore DELETEs its entire scope
--  (one sec_type, or one code in --code mode) and recompu tes every
--  episode from the FULL per-code history on every pipeline run;
--  --force additionally truncates the table first. Populated by the
--  internal market-hypes step of `analyze.mov_ave_spread` (see
--  market_hypes.py; reuses the parent source DataFrame's
--  trading_amount + std_{W}days columns — no second DB round-trip).
-- ----------------------------------------------------------------------------
DROP TABLE IF EXISTS analysis.mov_ave_market_hypes CASCADE;
DELETE FROM analysis.analysis_identity WHERE name = 'mov_ave_market_hypes';

CREATE TABLE analysis.mov_ave_market_hypes (
    sec_type                        TEXT         NOT NULL,  -- 'etf' | 'index' | 'stock'
    code                            TEXT         NOT NULL,
    start_date                      DATE         NOT NULL,
    end_date                        DATE         NOT NULL,
    min_checkin_period              INTEGER      NOT NULL,  -- 5 | 20 | 60 | 120 | 255
    hype_days                       INTEGER      NOT NULL,

    min_checkin_satisfaction_threshold NUMERIC(6,4) NOT NULL DEFAULT 60.0,
    min_trading_amt_threshold          NUMERIC(6,4) NOT NULL DEFAULT 60.0,
    trading_amt_hype_days              INTEGER      NOT NULL,
    min_std_threshold                  NUMERIC(6,4) NOT NULL DEFAULT 30.0,
    std_hype_days                      INTEGER      NOT NULL,

    CONSTRAINT pk_mov_ave_market_hypes PRIMARY KEY (code, sec_type, start_date, end_date, min_checkin_period)
) PARTITION BY HASH (code);

-- Native hash partitions (8) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('analysis', 'mov_ave_market_hypes', 8);

COMMENT ON TABLE  analysis.mov_ave_market_hypes              IS 'Market-hype EPISODE detector: one row per (code, sec_type, min_checkin_period, episode) — a CONCATENATED hype episode: a maximal span of trading dates anchored on a maximal run of consecutive hyped dates and extended through the surrounding check-in evidence (the W rows before the run''s first hyped date, back to its first check-in, and the W rows after the last hyped date, to its last check-in). A date is hyped when, within the last min_checkin_period (W) trading rows ending at it, MORE than min_checkin_satisfaction_threshold percent of the dates are check-ins — a check-in being a date whose daily trading_amount exceeds its centered-20-year min_trading_amt_threshold percentile AND whose W-day rolling population σ (std_{W}days) exceeds its centered-20-year min_std_threshold percentile (strict > on both legs; matching timescale: the σ window equals the check-in window). The audit base window is CENTERED on each audited date — ~2550 trading rows (10 trading years) before the date plus ~2550 rows after it (NOT a trailing/rolling-back window); windows with < 255 observations have no thresholds -> the date is not hyped; bases near the start/end of a code''s history are naturally truncated (newest dates have no future rows yet). Episodes are BUCKETED BY SPAN: min_checkin_period is the bucket minimum, the next window the exclusive maximum (20d: 20..59 rows; 60d: 60..119; 120d: 120..254; 255d: 255..5100 = the whole ±10y base) — one calendar turmoil lands in exactly the bucket matching its length. Non-hyped dates leave no footprint; episodes are REBUILT WHOLESALE per sec_type on every run of analyze.mov_ave_spread (new dates shift episode boundaries — the margin_changes precedent). sec_type ∈ {etf, index, stock}; one episode set per check-in window (5/20/60/120/255).';
COMMENT ON COLUMN analysis.mov_ave_market_hypes.sec_type   IS 'Security type: etf (ETF), index (CSI-style index), or stock (individual equity).';
COMMENT ON COLUMN analysis.mov_ave_market_hypes.code         IS 'Ticker. ETFs use exchange suffix (e.g. "510050.SS"); indices use bare code (e.g. "000300").';
COMMENT ON COLUMN analysis.mov_ave_market_hypes.start_date   IS 'Episode start date (inclusive): the earliest date of the episode''s CONCATENATED span — the FIRST check-in within the W-row lookback evidence window ending at the core run''s first hyped date (so episodes start at a turmoil''s first big-move day, not ~W rows later when the trailing satisfaction count finally crosses the threshold).';
COMMENT ON COLUMN analysis.mov_ave_market_hypes.end_date     IS 'Episode end date (inclusive): the latest date of the episode''s CONCATENATED span — the LAST check-in within the W-row lookforward window starting at the core run''s last hyped date (the decaying tail). A code''s trailing episode extends as new hyped dates arrive — the build deletes + recomputes its whole scope, so stored boundaries are always wholesale-rebuilt. >= start_date.';
COMMENT ON COLUMN analysis.mov_ave_market_hypes.min_checkin_period                 IS 'Check-in window length W in trading rows: the count of check-in dates is taken over the last W rows ending at each audited date (inclusive), and W doubles as the bucket''s MINIMUM episode span (the next window is the exclusive maximum; the 255d bucket is bounded by the whole ±10y base instead). Allowed values 5 / 20 / 60 / 120 / 255 — one episode set per window; the volatility leg uses the matching-timescale σ (std_{W}days). Part of the PK (two windows can produce episodes with identical spans).';
COMMENT ON COLUMN analysis.mov_ave_market_hypes.hype_days    IS 'Episode SPAN in TRADING dates: the number of trading rows from start_date to end_date inclusive — the CONCATENATED length (check-in days PLUS bridged interior gaps), bucket-filtered to [min_checkin_period, next check-in window) so each calendar turmoil lands in exactly the bucket matching its length.';
COMMENT ON COLUMN analysis.mov_ave_market_hypes.min_checkin_satisfaction_threshold IS 'Required percentage (0-100] of check-in dates within the window, strict greater-than. Default 60.0 = more than 60% of the W dates must be check-ins. Recorded build parameter — NOT part of the PK; changing it requires a --force rebuild.';
COMMENT ON COLUMN analysis.mov_ave_market_hypes.min_trading_amt_threshold          IS 'Centered-20-year PERCENTILE level (0-100, linear interpolation) of daily trading_amount used as the liquidity-leg threshold: a date checks in on this leg when its trading_amount exceeds the percentile of its centered window (~2550 rows before the date + ~2550 rows after it, ±10 trading years). Default 60.0 (60th percentile). Recorded build parameter — NOT part of the PK; changing it requires a --force rebuild.';
COMMENT ON COLUMN analysis.mov_ave_market_hypes.trading_amt_hype_days              IS 'Days within the episode span (start_date..end_date inclusive) on which the LIQUIDITY leg individually checked in (trading_amount > its centered-20y percentile). Diagnostic for which leg drove the episode; <= hype_days (interior bridged gaps did not check in).';
COMMENT ON COLUMN analysis.mov_ave_market_hypes.min_std_threshold                  IS 'Centered-20-year PERCENTILE level (0-100) of std_{W}days used as the volatility-leg threshold: a date checks in on this leg when its W-day rolling population σ exceeds the percentile of its centered window (~2550 rows before the date + ~2550 rows after it, ±10 trading years). Default 30.0 (30th percentile) — DELIBERATELY LOW: the W-day trailing σ lags a sudden turmoil by construction (the window still holds W-1 pre-turmoil rows on day 1), so a low bar lets episodes start at the turmoil''s first big-move day (the 2024-09-24 rally audit showed a 60th-pct σ leg delayed episode starts by a full month while the amt leg fired from day one). Recorded build parameter — NOT part of the PK; changing it requires a --force rebuild.';
COMMENT ON COLUMN analysis.mov_ave_market_hypes.std_hype_days                      IS 'Days within the episode span (start_date..end_date inclusive) on which the VOLATILITY leg individually checked in (std_{W}days > its centered-20y percentile). Diagnostic for which leg drove the episode; <= hype_days (interior bridged gaps did not check in).';

-- Migrate: add the per-leg check-in day counts to pre-existing installs
-- (CREATE TABLE includes them for fresh installs, but ADD COLUMN IF NOT
-- EXISTS retro-fits the columns to an already-existing table without
-- dropping data). Days within the episode span on which each leg
-- individually checked in — diagnostics for which leg drove the episode
-- (the volatility leg lags turmoil onsets, the liquidity leg fires from
-- day one). Nullable in the migration only because the column cannot be
-- added NOT NULL to a non-empty table; the wholesale per-sec_type rebuild
-- that the market-hypes step performs on every pipeline run refills every
-- row (run --force to trigger it immediately).
ALTER TABLE analysis.mov_ave_market_hypes ADD COLUMN IF NOT EXISTS trading_amt_hype_days INTEGER;
ALTER TABLE analysis.mov_ave_market_hypes ADD COLUMN IF NOT EXISTS std_hype_days           INTEGER;