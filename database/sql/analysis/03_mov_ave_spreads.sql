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
--  NOTE: Foreign-key constraints to the source OHLCV / MA tables are NOT
--  enforced because the source tables differ per sec_type (etf_* vs
--  index_* vs stock_*). Data integrity is guaranteed by the build script's
--  INNER JOINs on both basic_stats and tech_stats for each security type.
-- ============================================================================

-- Drop any prior version of these tables (ETF-only etf_mov_ave_spreads_* and
-- earlier wide-format revisions). Also remove the old analysis_identity row
-- so the new mov_ave_spread registration starts clean.
-- peaks_and_floors MUST be dropped AFTER detail (or CASCADE) because detail
-- has an FK to peaks_and_floors; CASCADE handles both orderings safely.
DROP TABLE IF EXISTS analysis.mov_ave_spreads_detail CASCADE;
DROP TABLE IF EXISTS analysis.mov_ave_spreads_detail_ema CASCADE;
DROP TABLE IF EXISTS analysis.mov_ave_spreads_detail_ohlc CASCADE;
DROP TABLE IF EXISTS analysis.mov_ave_peaks_and_floors CASCADE;
DROP TABLE IF EXISTS analysis.mov_ave_large_swings CASCADE;
DROP TABLE IF EXISTS analysis.mov_ave_rsi CASCADE;
DROP TABLE IF EXISTS analysis.etf_mov_ave_spreads_detail;
DELETE FROM analysis.analysis_identity WHERE name = 'etf_mov_ave_spread';
DELETE FROM analysis.analysis_identity WHERE name = 'mov_ave_rsi';
DELETE FROM analysis.analysis_identity WHERE name = 'mov_ave_spread_ema';
DELETE FROM analysis.analysis_identity WHERE name = 'mov_ave_spread_ohlc';

-- ----------------------------------------------------------------------------
--  Table: analysis.mov_ave_spreads_detail  (WIDE format)
--    One row per (sec_type, code, date) holding all 9 gap values.
--
--  Columns:
--    sec_type, code, date              — PK; identifies the asset universe
--                                            (etf vs index), ticker, and date
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

    -- FK to analysis.mov_ave_peaks_and_floors(sec_type, code, date).
    -- "Nearest preceding extreme" mapping: for each detail row at date D,
    -- peaks_and_floors_date = the largest extreme date <= D (most recent
    -- peak/floor on or before D). NULL only when no extreme exists before
    -- D (e.g. early history before the first belt). Constrained by a
    -- DEFERRABLE FK so the build script can insert peaks_and_floors +
    -- detail in any order within a single transaction.
    peaks_and_floors_date DATE,

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

    CONSTRAINT pk_mov_ave_spreads_detail PRIMARY KEY (sec_type, code, date),
    CONSTRAINT chk_mov_ave_spreads_detail_sec_type
        CHECK (sec_type IN ('etf', 'index', 'stock'))
);

-- NOTE: no separate (sec_type, code, date) index — the PK already covers
-- that lookup. A duplicate index was previously created here and dropped
-- because it doubled index-maintenance cost on every INSERT for zero
-- benefit (PK B-tree already serves equality + range scans on the
-- (sec_type, code, date) prefix).
--
-- Index for the FK lookups + monthly group-by queries (e.g. "all detail
-- rows belonging to a given peaks_and_floors row").
CREATE INDEX idx_mov_ave_spreads_detail_pf_date
    ON analysis.mov_ave_spreads_detail (sec_type, code, peaks_and_floors_date);

COMMENT ON TABLE  analysis.mov_ave_spreads_detail              IS 'MA-spread detail (WIDE format): one row per (sec_type, code, date) with 9 gap_value columns (5 Price/MA + 4 MA5/MA). sec_type ∈ {etf, index, stock}.';
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
--  No FK to mov_ave_spreads_detail — data integrity is guaranteed by
--  INNER JOINs on tech_stats in the build script.
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

    CONSTRAINT pk_mov_ave_spreads_detail_ema PRIMARY KEY (sec_type, code, date),
    CONSTRAINT chk_mov_ave_spreads_detail_ema_sec_type
        CHECK (sec_type IN ('etf', 'index', 'stock'))
);

COMMENT ON TABLE  analysis.mov_ave_spreads_detail_ema              IS 'EMA-spread detail (WIDE format): one row per (sec_type, code, date) with 9 EMA gap columns (5 Price/EMA + 4 EMA6/EMA) + 5 EMA slope + 5 EMA curvature columns. sec_type ∈ {etf, index, stock}. Source: stats.{etf,index,stock}_tech_stats.ema{6,20,60,120,255}.';
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
--  Table: analysis.mov_ave_peaks_and_floors  (per-EXTREME-DATE cadence)
--    One row per (sec_type, code, extreme_date). `date` IS the extreme date
--    itself — the actual biz date on which a local minimum or maximum close
--    price was observed within a "continuous belt" (see
--    analyze.mov_ave_spread.peaks_and_floors). The detail table
--    analysis.mov_ave_spreads_detail references this table via its
--    `peaks_and_floors_date` FK column using a "nearest preceding extreme"
--    mapping: each detail row at date D maps to the largest extreme date
--    <= D (NULL only when no extreme exists before D).
--
--  Columns:
--    sec_type, code, date  — PK; identifies the asset universe and the
--                            extreme biz date (NOT a month-start).
--    extreme_val           — the local min or max close price observed on
--                            `date`. Always NOT NULL (every row is an
--                            extreme). Both minima (valley lows / floors)
--                            and maxima (peaks) are detected.
--    nearby_extreme_date   — (floors only) the furthest date within the
--                            PREVIOUS 30 trading days of `date` whose
--                            OHLC low is strictly lower than the
--                            valley_low's OHLC high. NULL when no
--                            qualifying date exists, and NULL for peaks
--                            (only floors compute it). Backward-only
--                            (causal — no future data).
--    is_extreme_peak_not_floor — TRUE when this extreme is a local MAX
--                            (peak — upward trend). FALSE when this
--                            extreme is a local MIN (valley low / floor
--                            — downward trend). NOT NULL.
--
--  Cadence: ONE row per detected extreme. CAUSAL algorithm (no future
--  data used for the peak/floor judgement). A day D is a floor candidate
--  when its close is the trailing 60-day MINIMUM (lowest in [D-59, D])
--  AND D is inside a downward belt (close < MA60 − 2σ, OR close < MA60
--  for a causally-bridged run > 20 days; interruptions < 5 days bridged,
--  >= 5 break). Peak candidates are symmetric (trailing 60-day MAX +
--  upward belt). Cross-kind candidates within 5 PREVIOUS trading days
--  are dropped (oscillating/flat region). Same-kind candidates within
--  30 trading days are clustered, keeping the most extreme per cluster
--  (min for floors, max for peaks). Non-extreme dates have no
--  peaks_and_floors row; detail.peaks_and_floors_date is NULL for them.
-- ----------------------------------------------------------------------------


-- ----------------------------------------------------------------------------
--  Table: analysis.mov_ave_spreads_detail_ohlc  (OHLC summary per window)
--    One row per (sec_type, code, date) with today_close + rolling OHLC
--    stats over 6 windows (20/60/120/255/500/750 trading days).
--
--  Columns:
--    sec_type, code, date              — PK; identifies the asset universe
--                                        (etf vs index), ticker, and date
--    today_close                        — close price on `date`
--    For each window W ∈ {20, 60, 120, 255, 500, 750}:
--      open_Wd   — open price on the W-th trading day before `date`
--      high_Wd   — max high over the W trading days ending on `date`
--      low_Wd    — min low over the W trading days ending on `date`
--
--  NULL when not enough history (< W prior rows for the window).
--  Populated by the internal OHLC step of `analyze.mov_ave_spread`
--  (see ohlc.py). Reuses the same source DataFrame as the parent
--  pipeline — no second DB round-trip.
-- ----------------------------------------------------------------------------
CREATE TABLE analysis.mov_ave_spreads_detail_ohlc (
    sec_type          TEXT         NOT NULL,  -- 'etf' | 'index' | 'stock'
    code              TEXT         NOT NULL,
    date              DATE         NOT NULL,

    today_close       NUMERIC(18,6) NOT NULL,

    open_20d          NUMERIC(18,6),
    high_20d          NUMERIC(18,6),
    low_20d           NUMERIC(18,6),

    open_60d          NUMERIC(18,6),
    high_60d          NUMERIC(18,6),
    low_60d           NUMERIC(18,6),

    open_120d         NUMERIC(18,6),
    high_120d         NUMERIC(18,6),
    low_120d          NUMERIC(18,6),

    open_255d         NUMERIC(18,6),
    high_255d         NUMERIC(18,6),
    low_255d          NUMERIC(18,6),

    open_500d         NUMERIC(18,6),
    high_500d         NUMERIC(18,6),
    low_500d          NUMERIC(18,6),

    open_750d         NUMERIC(18,6),
    high_750d         NUMERIC(18,6),
    low_750d          NUMERIC(18,6),

    CONSTRAINT pk_mov_ave_spreads_detail_ohlc
        PRIMARY KEY (sec_type, code, date),
    CONSTRAINT chk_mov_ave_spreads_detail_ohlc_sec_type
        CHECK (sec_type IN ('etf', 'index', 'stock'))
);

COMMENT ON TABLE  analysis.mov_ave_spreads_detail_ohlc              IS 'OHLC detail: one row per (sec_type, code, date) with today_close + rolling open/high/low over 6 windows (20/60/120/255/500/750 trading days). sec_type ∈ {etf, index, stock}. Source: same DataFrame as mov_ave_spread parent pipeline (no second DB round-trip).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ohlc.sec_type     IS 'Security type: etf (ETF), index (CSI-style index), or stock (individual equity).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ohlc.code        IS 'Ticker. ETFs use exchange suffix (e.g. "510050.SS"); indices use bare code (e.g. "000300").';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ohlc.date        IS 'Business date (trading day).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ohlc.today_close IS 'Close price on `date` (COALESCE(adj_close, close) for ETFs; close for index/stock). NOT NULL.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ohlc.open_20d   IS 'Open price on the 20th trading day before `date`. NULL if fewer than 20 prior rows exist.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ohlc.high_20d   IS 'Max high price over the 20 trading days ending on `date`. NULL if fewer than 20 prior rows exist.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ohlc.low_20d    IS 'Min low price over the 20 trading days ending on `date`. NULL if fewer than 20 prior rows exist.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ohlc.open_60d   IS 'Open price on the 60th trading day before `date`. NULL if fewer than 60 prior rows exist.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ohlc.high_60d   IS 'Max high price over the 60 trading days ending on `date`. NULL if fewer than 60 prior rows exist.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ohlc.low_60d    IS 'Min low price over the 60 trading days ending on `date`. NULL if fewer than 60 prior rows exist.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ohlc.open_120d  IS 'Open price on the 120th trading day before `date`. NULL if fewer than 120 prior rows exist.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ohlc.high_120d  IS 'Max high price over the 120 trading days ending on `date`. NULL if fewer than 120 prior rows exist.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ohlc.low_120d   IS 'Min low price over the 120 trading days ending on `date`. NULL if fewer than 120 prior rows exist.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ohlc.open_255d  IS 'Open price on the 255th trading day before `date`. NULL if fewer than 255 prior rows exist.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ohlc.high_255d  IS 'Max high price over the 255 trading days ending on `date`. NULL if fewer than 255 prior rows exist.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ohlc.low_255d   IS 'Min low price over the 255 trading days ending on `date`. NULL if fewer than 255 prior rows exist.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ohlc.open_500d  IS 'Open price on the 500th trading day before `date`. NULL if fewer than 500 prior rows exist.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ohlc.high_500d  IS 'Max high price over the 500 trading days ending on `date`. NULL if fewer than 500 prior rows exist.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ohlc.low_500d   IS 'Min low price over the 500 trading days ending on `date`. NULL if fewer than 500 prior rows exist.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ohlc.open_750d  IS 'Open price on the 750th trading day before `date`. NULL if fewer than 750 prior rows exist.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ohlc.high_750d  IS 'Max high price over the 750 trading days ending on `date`. NULL if fewer than 750 prior rows exist.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ohlc.low_750d   IS 'Min low price over the 750 trading days ending on `date`. NULL if fewer than 750 prior rows exist.';

CREATE TABLE analysis.mov_ave_peaks_and_floors (
    sec_type          TEXT         NOT NULL,  -- 'etf' | 'index' | 'stock'
    code              TEXT         NOT NULL,
    date              DATE         NOT NULL,  -- extreme biz date (NOT month-start)

    extreme_val        NUMERIC(18,6)         NOT NULL,
    nearby_extreme_date DATE,
    is_extreme_peak_not_floor BOOLEAN         NOT NULL,

    CONSTRAINT pk_mov_ave_peaks_and_floors PRIMARY KEY (sec_type, code, date),
    CONSTRAINT chk_mov_ave_peaks_and_floors_sec_type
        CHECK (sec_type IN ('etf', 'index', 'stock'))
);

CREATE INDEX idx_mov_ave_peaks_and_floors_sec_type_code_date
    ON analysis.mov_ave_peaks_and_floors (sec_type, code, date);

COMMENT ON TABLE  analysis.mov_ave_peaks_and_floors             IS 'Peaks-and-floors analysis: one row per (sec_type, code, extreme_date). `date` is the extreme biz date (local min/max close); detail.mov_ave_spreads_detail.peaks_and_floors_date FK references this table (NULL for non-extreme dates).';
COMMENT ON COLUMN analysis.mov_ave_peaks_and_floors.sec_type    IS 'Security type: etf (ETF), index (CSI-style index), or stock (individual equity).';
COMMENT ON COLUMN analysis.mov_ave_peaks_and_floors.code        IS 'Ticker. ETFs use exchange suffix (e.g. "510050.SS"); indices use bare code (e.g. "000300").';
COMMENT ON COLUMN analysis.mov_ave_peaks_and_floors.date        IS 'Extreme biz date — the actual trading day on which a trailing-60-day local min/max close was observed inside a belt. PK column referenced by mov_ave_spreads_detail.peaks_and_floors_date.';
COMMENT ON COLUMN analysis.mov_ave_peaks_and_floors.extreme_val  IS 'The close price observed on `date` (a trailing-60-day min for floors / max for peaks). CAUSAL detection: a day qualifies as a floor candidate when its close is the lowest in the trailing 60 trading days AND it is inside a downward belt (close < MA60 − 2σ, OR close < MA60 for a causally-bridged run > 20 days, interruptions < 5 days bridged). Peaks are symmetric (trailing 60-day max + upward belt). Cross-kind candidates within 5 PREVIOUS trading days are dropped (oscillating region). Same-kind candidates within 30 trading days are clustered, keeping the most extreme per cluster (min for floors, max for peaks).';
COMMENT ON COLUMN analysis.mov_ave_peaks_and_floors.nearby_extreme_date IS 'The furthest date within the PREVIOUS 30 trading days of `date` (the valley_low_date) whose OHLC low is strictly lower than the valley_low''s OHLC high. NULL when no qualifying date exists in the backward 30 trading-day window, and NULL for peaks (only floors compute nearby_extreme_date). Backward-only (causal — no future data). Computed by analyze.mov_ave_spread.peaks_and_floors._compute_nearby_extreme_date.';
COMMENT ON COLUMN analysis.mov_ave_peaks_and_floors.is_extreme_peak_not_floor IS 'TRUE when this extreme is a local MAX (peak — trailing-60-day high inside an upward belt). FALSE when this extreme is a local MIN (valley low / floor — trailing-60-day low inside a downward belt). The frontend uses this to render up-triangles (green) for peaks and down-triangles (red) for floors.';

-- ----------------------------------------------------------------------------
--  Table: analysis.mov_ave_rsi  (per-asset+date Wilder RSI + short-term gaps)
--    One row per (sec_type, code, date). Stores Wilder's Relative Strength
--    Index for 4 windows (6/10/14/20 days) and 2 short-term price-gap
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
    -- Windows: 6 / 10 / 14 (classic Wilder) / 20 / 60 / 120 (~half trading
    -- year) / 255 (~1 trading year, matches MA255) / 500 (~2 trading years).
    rsi_6days       NUMERIC(10,6),
    rsi_10days      NUMERIC(10,6),
    rsi_14days      NUMERIC(10,6),
    rsi_20days      NUMERIC(10,6),
    rsi_60days      NUMERIC(10,6),
    rsi_120days     NUMERIC(10,6),
    rsi_255days     NUMERIC(10,6),
    rsi_500days     NUMERIC(10,6),

    -- 2 short-term price-gap (N-day return) columns
    gap_2days       NUMERIC(10,6), -- sign indicates last extreme is max or min
    gap_3days       NUMERIC(10,6), -- sign indicates last extreme is max or min 
    gap_since_last_extreme NUMERIC(10,6), -- sign indicates last extreme is max or min
    days_since_last_extreme NUMERIC(10,6),
    date_of_last_extreme DATE,

    CONSTRAINT pk_mov_ave_rsi PRIMARY KEY (sec_type, code, date),
    CONSTRAINT chk_mov_ave_rsi_sec_type
        CHECK (sec_type IN ('etf', 'index', 'stock'))
);

-- Migrate: add rsi_60days column to pre-existing installs (CREATE TABLE
-- includes it for fresh installs, but ADD COLUMN IF NOT EXISTS retro-fits
-- the column to an already-existing table without dropping data). Wilder
-- RSI over 60 trading days (alpha=1/60, ewm adjust=False,
-- min_periods=60) — a longer-term momentum window complementing the
-- classic 14-day Wilder default. 0..100. NULL until 60 consecutive
-- gain/loss observations. Built by analyze.mov_ave_spread (rsi.py).
ALTER TABLE analysis.mov_ave_rsi ADD COLUMN IF NOT EXISTS rsi_60days NUMERIC(10,6);

-- Migrate: add rsi_120days / rsi_255days / rsi_500days columns to pre-existing
-- installs. Wilder RSI over 120 / 255 / 500 trading days (alpha=1/N,
-- ewm adjust=False, min_periods=N) — progressively longer-term momentum
-- windows complementing the classic 14-day Wilder default. 255 days ≈ 1
-- trading year (matches the MA255 window); 500 days ≈ 2 trading years.
-- 0..100. NULL until N consecutive gain/loss observations. Built by
-- analyze.mov_ave_spread (rsi.py).
ALTER TABLE analysis.mov_ave_rsi ADD COLUMN IF NOT EXISTS rsi_120days NUMERIC(10,6);
ALTER TABLE analysis.mov_ave_rsi ADD COLUMN IF NOT EXISTS rsi_255days NUMERIC(10,6);
ALTER TABLE analysis.mov_ave_rsi ADD COLUMN IF NOT EXISTS rsi_500days NUMERIC(10,6);

-- NOTE: no separate (sec_type, code, date) index — the PK already covers
-- that lookup (same rationale as mov_ave_spreads_detail above). A duplicate
-- index was previously created here and dropped because it doubled index-
-- maintenance cost on every INSERT for zero benefit (PK B-tree already
-- serves equality + range scans on the (sec_type, code, date) prefix).

COMMENT ON TABLE  analysis.mov_ave_rsi             IS 'Wilder RSI (6/10/14/20/60/120/255/500 days) + short-term price gaps (2/3 day returns). One row per (sec_type, code, date). sec_type ∈ {etf, index, stock}.';
COMMENT ON COLUMN analysis.mov_ave_rsi.sec_type    IS 'Security type: etf (ETF), index (CSI-style index), or stock (individual equity).';
COMMENT ON COLUMN analysis.mov_ave_rsi.code        IS 'Ticker. ETFs use exchange suffix (e.g. "510050.SS"); indices use bare code (e.g. "000300").';
COMMENT ON COLUMN analysis.mov_ave_rsi.date        IS 'Business date (trading day).';
COMMENT ON COLUMN analysis.mov_ave_rsi.rsi_6days   IS 'Wilder RSI over 6 trading days (alpha=1/6, ewm adjust=False, min_periods=6). 0..100. NULL until 6 consecutive gain/loss observations.';
COMMENT ON COLUMN analysis.mov_ave_rsi.rsi_10days  IS 'Wilder RSI over 10 trading days (alpha=1/10, ewm adjust=False, min_periods=10). 0..100. NULL until 10 periods.';
COMMENT ON COLUMN analysis.mov_ave_rsi.rsi_14days  IS 'Wilder RSI over 14 trading days (alpha=1/14, ewm adjust=False, min_periods=14) — the classic Wilder window. 0..100. NULL until 14 periods.';
COMMENT ON COLUMN analysis.mov_ave_rsi.rsi_20days  IS 'Wilder RSI over 20 trading days (alpha=1/20, ewm adjust=False, min_periods=20). 0..100. NULL until 20 periods.';
COMMENT ON COLUMN analysis.mov_ave_rsi.rsi_60days  IS 'Wilder RSI over 60 trading days (alpha=1/60, ewm adjust=False, min_periods=60) — a longer-term momentum window complementing the classic 14-day Wilder default. 0..100. NULL until 60 consecutive gain/loss observations. Computed by analyze.mov_ave_spread (rsi.py) using the same Wilder EWM recurrence as the other RSI windows (delta = price[t]-price[t-1] is cuDF-accelerated via grouped_diff; the per-window EWM stays on pandas because cuDF lacks grouped-ewm support — see rsi.py for the documented rationale).';
COMMENT ON COLUMN analysis.mov_ave_rsi.rsi_120days IS 'Wilder RSI over 120 trading days (alpha=1/120, ewm adjust=False, min_periods=120) — a half-trading-year momentum window. 0..100. NULL until 120 consecutive gain/loss observations. Computed by analyze.mov_ave_spread (rsi.py) using the same Wilder EWM recurrence as the other RSI windows.';
COMMENT ON COLUMN analysis.mov_ave_rsi.rsi_255days IS 'Wilder RSI over 255 trading days (alpha=1/255, ewm adjust=False, min_periods=255) — a ~1-trading-year momentum window matching the MA255 window. 0..100. NULL until 255 consecutive gain/loss observations. Computed by analyze.mov_ave_spread (rsi.py) using the same Wilder EWM recurrence as the other RSI windows.';
COMMENT ON COLUMN analysis.mov_ave_rsi.rsi_500days IS 'Wilder RSI over 500 trading days (alpha=1/500, ewm adjust=False, min_periods=500) — a ~2-trading-year long-term momentum window. 0..100. NULL until 500 consecutive gain/loss observations. Computed by analyze.mov_ave_spread (rsi.py) using the same Wilder EWM recurrence as the other RSI windows. Useful for very long-term trend confirmation on indices and mature stocks; will be NULL for most recent IPOs / ETFs with < 500 rows of history.';
COMMENT ON COLUMN analysis.mov_ave_rsi.gap_2days   IS '2-day price return: (price[t] - price[t-2]) / price[t-2]. Signed fractional ratio. NULL for the first 2 rows of each code.';
COMMENT ON COLUMN analysis.mov_ave_rsi.gap_3days   IS '3-day price return: (price[t] - price[t-3]) / price[t-3]. Signed fractional ratio. NULL for the first 3 rows of each code.';
COMMENT ON COLUMN analysis.mov_ave_rsi.gap_since_last_extreme IS 'Signed fractional gap from the most recent local turning point (high/low) detected by price_slope sign change: (price[t] - extreme_price) / extreme_price. Sign indicates the type of the last extreme: positive = last extreme was a local MIN (price rebounded upward since the trough), negative = last extreme was a local MAX (price fell since the peak). NULL when no preceding turning point exists for the code (early history before the first turn).';
COMMENT ON COLUMN analysis.mov_ave_rsi.days_since_last_extreme IS 'Trading days since the most recent local turning point (high/low) detected by price_slope sign change. 0 on the extreme row itself. NULL when no preceding turning point exists for the code.';
COMMENT ON COLUMN analysis.mov_ave_rsi.date_of_last_extreme IS 'The biz date of the most recent local turning point (high/low) detected by price_slope sign change — i.e. the date on which the extreme_price referenced by gap_since_last_extreme was observed. Carried forward (forward-filled) from each turning point until the next one. NULL when no preceding turning point exists for the code (early history before the first turn).';


-- ----------------------------------------------------------------------------
--  Table: analysis.mov_ave_rebounds  (per-asset+date double-top detection)
--    One row per (sec_type, code, date). For each window W ∈ {20,60,120,255},
--    detects a "rebound" (double-top / shoulder pattern) within the trailing
--    W trading days:
--      1. Find the close-price maximum in [D-W+1, D] → "top max"
--      2. After the top max date, find the next close-price maximum within
--         the same window → "2nd max" (the rebound)
--      3. If the top max is NOT today and a 2nd max exists after it:
--           rebound_date_{W}days        = date of the 2nd max
--           rebound_close_price_{W}days = close at the 2nd max
--           rebound_gap_days_{W}days    = trading days between top max
--                                        and 2nd max
--           rebound_trading_amt_{W}days = SUM(trading_amount) during the
--                                        rebound period (top max → 2nd max)
--         All 4 columns are NULL when the top max is today or when no
--         2nd max exists after it (single-peak window).
--
--  Source: same DataFrame as the mov_ave_spread parent pipeline (price,
--  trading_amount columns — no second DB round-trip). Populated by the
--  internal rebounds step (see rebounds.py).
-- ----------------------------------------------------------------------------
DROP TABLE IF EXISTS analysis.mov_ave_rebounds CASCADE;
DELETE FROM analysis.analysis_identity WHERE name = 'mov_ave_rebounds';

CREATE TABLE analysis.mov_ave_rebounds (
    sec_type        TEXT         NOT NULL,  -- 'etf' | 'index' | 'stock'
    code            TEXT         NOT NULL,
    date            DATE         NOT NULL,

    rebound_date_20days DATE,
    rebound_close_price_20days  NUMERIC(10,6),
    rebound_gap_days_20days     NUMERIC(10,6),
    rebound_trading_amt_20days  NUMERIC(24,4),

    rebound_date_60days DATE,
    rebound_close_price_60days  NUMERIC(10,6),
    rebound_gap_days_60days     NUMERIC(10,6),
    rebound_trading_amt_60days  NUMERIC(24,4),

    rebound_date_120days DATE,
    rebound_close_price_120days  NUMERIC(10,6),
    rebound_gap_days_120days     NUMERIC(10,6),
    rebound_trading_amt_120days  NUMERIC(24,4),

    rebound_date_255days DATE,
    rebound_close_price_255days  NUMERIC(10,6),
    rebound_gap_days_255days     NUMERIC(10,6),
    rebound_trading_amt_255days  NUMERIC(24,4),

    CONSTRAINT pk_mov_ave_rebounds PRIMARY KEY (sec_type, code, date),
    CONSTRAINT chk_mov_ave_rebounds_sec_type
        CHECK (sec_type IN ('etf', 'index', 'stock'))
);

COMMENT ON TABLE  analysis.mov_ave_rebounds IS 'Double-top (rebound) detection analysis: one row per (sec_type, code, date). For each window W ∈ {20,60,120,255}, detects a rebound pattern (2nd max close after the top max within the trailing W days). sec_type ∈ {etf, index, stock}.';
COMMENT ON COLUMN analysis.mov_ave_rebounds.sec_type IS 'Security type: etf (ETF), index (CSI-style index), or stock (individual equity).';
COMMENT ON COLUMN analysis.mov_ave_rebounds.code IS 'Ticker. ETFs use exchange suffix (e.g. "510050.SS"); indices use bare code (e.g. "000300").';
COMMENT ON COLUMN analysis.mov_ave_rebounds.date IS 'Business date (trading day).';
COMMENT ON COLUMN analysis.mov_ave_rebounds.rebound_date_20days IS 'Date of the 2nd max close (rebound peak) within the trailing 20-day window. NULL when no rebound pattern exists (today is the window max or no second peak).';
COMMENT ON COLUMN analysis.mov_ave_rebounds.rebound_close_price_20days IS 'Close price at the 2nd max (rebound peak) within the trailing 20-day window. NUMERIC(10,6). NULL when no rebound.';
COMMENT ON COLUMN analysis.mov_ave_rebounds.rebound_gap_days_20days IS 'Trading days between the top max and the 2nd max (rebound) within the trailing 20-day window. NUMERIC(10,6). NULL when no rebound.';
COMMENT ON COLUMN analysis.mov_ave_rebounds.rebound_trading_amt_20days IS 'SUM of trading_amount during the rebound period (top max → 2nd max, inclusive) within the trailing 20-day window. NUMERIC(24,4). NULL when no rebound.';
COMMENT ON COLUMN analysis.mov_ave_rebounds.rebound_date_60days IS 'Date of the 2nd max close (rebound peak) within the trailing 60-day window. NULL when no rebound.';
COMMENT ON COLUMN analysis.mov_ave_rebounds.rebound_close_price_60days IS 'Close price at the 2nd max within the trailing 60-day window. NULL when no rebound.';
COMMENT ON COLUMN analysis.mov_ave_rebounds.rebound_gap_days_60days IS 'Trading days between top max and 2nd max within the trailing 60-day window. NULL when no rebound.';
COMMENT ON COLUMN analysis.mov_ave_rebounds.rebound_trading_amt_60days IS 'SUM of trading_amount during the rebound period within the trailing 60-day window. NUMERIC(24,4). NULL when no rebound.';
COMMENT ON COLUMN analysis.mov_ave_rebounds.rebound_date_120days IS 'Date of the 2nd max close (rebound peak) within the trailing 120-day window. NULL when no rebound.';
COMMENT ON COLUMN analysis.mov_ave_rebounds.rebound_close_price_120days IS 'Close price at the 2nd max within the trailing 120-day window. NULL when no rebound.';
COMMENT ON COLUMN analysis.mov_ave_rebounds.rebound_gap_days_120days IS 'Trading days between top max and 2nd max within the trailing 120-day window. NULL when no rebound.';
COMMENT ON COLUMN analysis.mov_ave_rebounds.rebound_trading_amt_120days IS 'SUM of trading_amount during the rebound period within the trailing 120-day window. NUMERIC(24,4). NULL when no rebound.';
COMMENT ON COLUMN analysis.mov_ave_rebounds.rebound_date_255days IS 'Date of the 2nd max close (rebound peak) within the trailing 255-day window. NULL when no rebound.';
COMMENT ON COLUMN analysis.mov_ave_rebounds.rebound_close_price_255days IS 'Close price at the 2nd max within the trailing 255-day window. NULL when no rebound.';
COMMENT ON COLUMN analysis.mov_ave_rebounds.rebound_gap_days_255days IS 'Trading days between top max and 2nd max within the trailing 255-day window. NULL when no rebound.';
COMMENT ON COLUMN analysis.mov_ave_rebounds.rebound_trading_amt_255days IS 'SUM of trading_amount during the rebound period within the trailing 255-day window. NUMERIC(24,4). NULL when no rebound.';

-- ----------------------------------------------------------------------------
--  Table: analysis.mov_ave_trading_amt  (WIDE format — trading-amount metrics)
--    One row per (sec_type, code, date). Extracted from
--    mov_ave_spreads_detail and extended with rolling max/min + ratio columns.
--
--  Columns (grouped by semantic purpose):
--
--  1. Trading-amount MAs (yuan, NUMERIC(24,4)):
--     trading_amt_ma{5,20,60,120,255} — simple moving average of
--     trading_amount per (sec_type, code). Source:
--     stats.{etf_liquidity_margin,index_basic_stats,stock_liquidity_margin}
--     .trading_amount. NULL until W consecutive rows.
--
--  2. Rolling max/min of trading_amt_ma5 over N days (NUMERIC(24,4)):
--     trading_amt_ma5_max_over_{20,60,120,255}days — rolling max of
--     trading_amt_ma5 over the past N trading days. NULL until N rows.
--     trading_amt_ma5_min_over_{20,60,120,255}days — rolling min.
--
--  3. Ratio columns (NUMERIC(10,4)):
--     trading_amt_today_vs_trading_amt_ma5_max_over_{N}days_ratio —
--         today's trading_amount / rolling max of trading_amt_ma5
--         over N days. >1 means today's volume exceeds the recent peak
--         in the MA5. NULL when today's amt or rolling max is NULL or
--         max <= 0.
--     trading_amt_ma5_vs_trading_amt_ma5_max_over_{N}days_ratio —
--         trading_amt_ma5 / rolling max of trading_amt_ma5 over N days.
--         How close the current MA5 is to its recent peak.
--         NULL when either is NULL or max <= 0.
--
--  4. Trading-amount market-share MAs (NUMERIC(10,4)):
--     trading_amt_market_share_ma{5,20,60,120,255} — market_share =
--     trading_amount / market_denominator. Then W-day MA of market_share
--     per (sec_type, code).
--
--  5. Trading-amount MA slopes (NUMERIC(10,4)):
--     trading_amt_ma{5,20,60,120,255}_slope — fractional daily change
--     (ma[t]-ma[t-1])/ma[t-1].
--
--  6. Trading-amount market-share-vs-MA gaps (NUMERIC(10,4)):
--     trading_amt_market_share_vs_ma{5,20,60,120,255} — signed fractional
--     gap (market_share - market_share_ma{W}) / market_share_ma{W}.
--
--  Populated by the internal trading-amt step of `analyze.mov_ave_spread`
--  (see trading_amt.py). Incremental upsert by missing dates;
--  --force truncates first.
-- ----------------------------------------------------------------------------

DROP TABLE IF EXISTS analysis.mov_ave_trading_amt CASCADE;
DROP TABLE IF EXISTS analysis.mov_ave_trading_amt_ext CASCADE;
DELETE FROM analysis.analysis_identity WHERE name = 'mov_ave_trading_amt';

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

    trading_amt_vs_price_slope_ratio         NUMERIC(10,4), -- indicate for how much capital could push for what extent of price changes
    trading_amt_ma5_vs_price_ma5_slope_ratio     NUMERIC(10,4),
    trading_amt_ma20_vs_price_ma20_slope_ratio    NUMERIC(10,4),
    trading_amt_ma60_vs_price_ma60_slope_ratio    NUMERIC(10,4),
    trading_amt_ma120_vs_price_ma120_slope_ratio   NUMERIC(10,4),
    trading_amt_ma255_vs_price_ma255_slope_ratio   NUMERIC(10,4),

    trading_amt_market_share_vs_ma5     NUMERIC(10,4),
    trading_amt_market_share_vs_ma20    NUMERIC(10,4),
    trading_amt_market_share_vs_ma60    NUMERIC(10,4),
    trading_amt_market_share_vs_ma120   NUMERIC(10,4),
    trading_amt_market_share_vs_ma255   NUMERIC(10,4),

    CONSTRAINT pk_mov_ave_trading_amt PRIMARY KEY (sec_type, code, date),
    CONSTRAINT chk_mov_ave_trading_amt_sec_type
        CHECK (sec_type IN ('etf', 'index', 'stock'))
);

COMMENT ON TABLE  analysis.mov_ave_trading_amt              IS 'Trading-amount analysis (WIDE format): one row per (sec_type, code, date) with 5 trading-amount MA columns + 5 trading-amount Bollinger band σ columns (rolling population std of trading_amt_maW over W days) + 5 market-share MA columns + 5 MA slope columns + 5 market-share-vs-MA gap columns. sec_type ∈ {etf, index, stock}.';
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
COMMENT ON COLUMN analysis.mov_ave_trading_amt.trading_amt_vs_price_slope_ratio       IS 'Liquidity-impact proxy: (trading_amount / 1,000,000) / price_slope. How many millions of capital push price by one unit. price_slope=0 auto-set to 1.0 to avoid division-by-zero. NULL when trading_amount or price_slope is NULL.';
COMMENT ON COLUMN analysis.mov_ave_trading_amt.trading_amt_ma5_vs_price_ma5_slope_ratio   IS '(trading_amt_ma5 / 1,000,000) / ma5_slope. Millions of capital (MA5 timescale) per unit of MA5 price change. ma5_slope=0 auto-set to 1.0.';
COMMENT ON COLUMN analysis.mov_ave_trading_amt.trading_amt_ma20_vs_price_ma20_slope_ratio  IS '(trading_amt_ma20 / 1,000,000) / ma20_slope. Millions of capital (MA20 timescale) per unit of MA20 price change. ma20_slope=0 auto-set to 1.0.';
COMMENT ON COLUMN analysis.mov_ave_trading_amt.trading_amt_ma60_vs_price_ma60_slope_ratio  IS '(trading_amt_ma60 / 1,000,000) / ma60_slope. Millions of capital (MA60 timescale) per unit of MA60 price change. ma60_slope=0 auto-set to 1.0.';
COMMENT ON COLUMN analysis.mov_ave_trading_amt.trading_amt_ma120_vs_price_ma120_slope_ratio IS '(trading_amt_ma120 / 1,000,000) / ma120_slope. Millions of capital (MA120 timescale) per unit of MA120 price change. ma120_slope=0 auto-set to 1.0.';
COMMENT ON COLUMN analysis.mov_ave_trading_amt.trading_amt_ma255_vs_price_ma255_slope_ratio IS '(trading_amt_ma255 / 1,000,000) / ma255_slope. Millions of capital (MA255 timescale) per unit of MA255 price change. ma255_slope=0 auto-set to 1.0.';
COMMENT ON COLUMN analysis.mov_ave_trading_amt.trading_amt_market_share_vs_ma5    IS '(market_share - market_share_ma5) / market_share_ma5.';
COMMENT ON COLUMN analysis.mov_ave_trading_amt.trading_amt_market_share_vs_ma20   IS '(market_share - market_share_ma20) / market_share_ma20.';
COMMENT ON COLUMN analysis.mov_ave_trading_amt.trading_amt_market_share_vs_ma60   IS '(market_share - market_share_ma60) / market_share_ma60.';
COMMENT ON COLUMN analysis.mov_ave_trading_amt.trading_amt_market_share_vs_ma120  IS '(market_share - market_share_ma120) / market_share_ma120.';
COMMENT ON COLUMN analysis.mov_ave_trading_amt.trading_amt_market_share_vs_ma255  IS '(market_share - market_share_ma255) / market_share_ma255.';