-- ============================================================================
--  mov_ave_spread suite — split of the former 03_mov_ave_spreads.sql.
--  Shared conventions (sec_type handling, the NO-FK derived-data design,
--  rebuild semantics): see 01_mov_ave_spreads_detail.sql.
-- ============================================================================

-- Drop any prior version so a re-run rebuilds the table from the CREATE
-- below; also remove the stale analysis_identity row so the registration
-- starts clean.
DROP TABLE IF EXISTS analysis.mov_ave_spreads_detail_ema CASCADE;
DELETE FROM analysis.analysis_identity WHERE name = 'mov_ave_spread_ema';

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
