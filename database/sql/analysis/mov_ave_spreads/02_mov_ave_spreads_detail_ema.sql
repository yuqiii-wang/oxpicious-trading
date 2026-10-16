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
--  asset+code+date, 9 EMA gap columns + 2 EMA slope + 2 EMA curvature)
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
--  2 EMA slope columns (1st derivative = group-diff per (sec_type, code)
--  ordered by date) + 2 EMA curvature columns (2nd derivative = diff of
--  slope), windows 6/20 only (2026-09: the derivatives were trimmed to
--  nothing, then restored for the 6/20-day legs — slope = emaW[t] −
--  emaW[t−1], a DIRECT diff of the EMA line, curvature = that slope's
--  diff). NULL on first date (slope) / first two dates (curvature) of
--  each code. The price leg's derivatives live on the SMA detail table
--  (mov_ave_spreads_detail.price_slope).
--
--  Populated by the internal EMA step of `analysis.mov_ave_spread` (see
--  ema.py). Incremental upsert by missing dates; --force truncates first.
-- ----------------------------------------------------------------------------
CREATE TABLE analysis.mov_ave_spreads_detail_ema (
    sec_type          SMALLINT     NOT NULL,  -- label code (static.label_dim 'sec_type'): 0=etf 1=index 2=stock
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

    -- 2 EMA slope columns (1st derivative per (sec_type, code) by date) +
    -- 2 EMA curvature columns — windows 6/20 only, direct diffs of the
    -- EMA line values.
    ema6_slope        NUMERIC(10,6),
    ema20_slope       NUMERIC(10,6),

    ema6_curvature    NUMERIC(10,6),
    ema20_curvature   NUMERIC(10,6),

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

COMMENT ON TABLE  analysis.mov_ave_spreads_detail_ema              IS 'EMA-spread detail (WIDE format): one row per (code, sec_type, date) with 9 EMA gap columns (5 Price/EMA + 4 EMA6/EMA) + 2 EMA slope + 2 EMA curvature columns (windows 6/20, direct diffs of the EMA line) + 5 rolling σ columns. sec_type ∈ {etf, index, stock}. Source: stats.{etf,index,stock}_tech_stats.ema{6,20,60,120,255}.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ema.sec_type     IS 'Security type as its static.label_dim (''sec_type'' domain) SMALLINT code: 0 = etf (ETF), 1 = index (CSI-style index), 2 = stock (individual equity).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ema.code         IS 'Ticker. ETFs bare 6-digit (e.g. "510050"); indices bare code (e.g. "000300"); stocks keep their exchange suffix (e.g. "600008.SS").';
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
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ema.ema6_slope        IS '1st derivative of the EMA6 LINE (EMA6[t] - EMA6[t-1]) per trading day — a direct diff of the EMA values, not an average of the close-price diff. NULL on the first date of each code.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ema.ema20_slope       IS '1st derivative of the EMA20 LINE (EMA20[t] - EMA20[t-1]) per trading day. NULL on the first date of each code.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ema.ema6_curvature    IS '2nd derivative of EMA6 (ema6_slope[t] - ema6_slope[t-1]). NULL on the first two dates of each code.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ema.ema20_curvature   IS '2nd derivative of EMA20 (ema20_slope[t] - ema20_slope[t-1]). NULL on the first two dates of each code.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ema.std_5days   IS 'Rolling population σ (ddof=0) of price over 5 trading days. Bollinger band width for the EMA6 envelope (EMA6 uses the 5-day σ as the closest available window). NULL until 5 consecutive rows. Same source data as analysis.mov_ave_spreads_detail.std_5days — populated from the parent pipeline so the EMA table is self-contained for Bollinger rendering without a JOIN back to the SMA detail table.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ema.std_20days  IS 'Rolling population σ (ddof=0) of price over 20 trading days. Bollinger band width for the EMA20 envelope. NULL until 20 consecutive rows. Same source data as analysis.mov_ave_spreads_detail.std_20days.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ema.std_60days  IS 'Rolling population σ (ddof=0) of price over 60 trading days. Bollinger band width for the EMA60 envelope. NULL until 60 consecutive rows. Same source data as analysis.mov_ave_spreads_detail.std_60days.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ema.std_120days IS 'Rolling population σ (ddof=0) of price over 120 trading days. Bollinger band width for the EMA120 envelope. NULL until 120 consecutive rows. Same source data as analysis.mov_ave_spreads_detail.std_120days.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ema.std_255days IS 'Rolling population σ (ddof=0) of price over 255 trading days. Bollinger band width for the EMA255 envelope. NULL until 255 consecutive rows. Same source data as analysis.mov_ave_spreads_detail.std_255days.';
