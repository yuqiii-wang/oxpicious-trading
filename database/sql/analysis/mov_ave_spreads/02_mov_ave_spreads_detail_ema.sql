-- ============================================================================
--  mov_ave_spread suite — split of the former 03_mov_ave_spreads.sql.
--  Shared conventions (sec_type handling, the NO-FK derived-data design,
--  rebuild semantics): see 01_mov_ave_spreads_detail.sql.
-- ============================================================================

-- Drop any prior version so a re-run rebuilds the table from the CREATE
-- below (the registry row re-registers via the runtime upsert_analysis_identity).
DROP TABLE IF EXISTS analysis.mov_ave_spreads_detail_ema CASCADE;

-- ----------------------------------------------------------------------------
--  Table: analysis.mov_ave_spreads_detail_ema  (WIDE: one row per
--  asset+code+date, 6 EMA gap columns + 2 EMA slope + 2 EMA curvature)
--
--  EMA counterpart of mov_ave_spreads_detail. Source: stats.{etf,index,
--  stock}_tech_stats.ema{6,12,26} (already fetched by the parent
--  mov_ave_spread pipeline — reuses the same source DataFrame, no second
--  DB round-trip; industry computes them in-pipeline with the same
--  conventions).
--
--  2026-10-26 the TYPICAL EMA rebase: the pair vocabulary covers exactly
--  the TYPICAL EMA spans {6, 12, 26} — 6 the A-share short, 12/26 the
--  classic MACD pair (precomputed in tech_stats since the same day,
--  replacing the nearby non-standard span 10). The SMA-convention
--  windows (20/60/120/255) stay in the Simple-MA section/detail table.
--
--  6 gap pairs (canonical order):
--    3 Price-vs-EMA pairs:  gap = (price - emaX) / emaX,  X ∈ {6,12,26}
--    3 EMA-vs-EMA pairs:    gap = (emaS - emaL) / emaL,
--                           (S,L) ∈ {(6,12) (6,26) (12,26)}
--                           — the classic 12/26 spread IS the MACD
--                           line's components.
--
--  2 EMA slope columns (1st derivative = group-diff per (sec_type, code)
--  ordered by date) + 2 EMA curvature columns (2nd derivative = diff of
--  slope), windows 6/12 only — the two shortest legs of the pair set
--  (the 26 leg carries no derivatives, the SMA table's 60/120/255
--  precedent). NULL on first date (slope) / first two dates (curvature)
--  of each code. The price leg's derivatives live on the SMA detail
--  table (mov_ave_spreads_detail.price_slope).
--
--  Populated by the internal EMA step of `analysis.mov_ave_spread` (see
--  ema.py). Incremental upsert by missing dates; --force truncates first.
-- ----------------------------------------------------------------------------
CREATE TABLE analysis.mov_ave_spreads_detail_ema (
    sec_type          SMALLINT     NOT NULL,  -- label code (static.label_dim 'sec_type'): 0=etf 1=index 2=stock 3=industry
    code              TEXT         NOT NULL,
    date              DATE         NOT NULL,

    -- 3 Price-vs-EMA gap columns
    price_vs_ema6     NUMERIC(10,6),
    price_vs_ema12    NUMERIC(10,6),
    price_vs_ema26    NUMERIC(10,6),

    -- 3 EMA-vs-EMA gap columns (the classic 12/26 pair = the MACD line)
    ema6_vs_ema12     NUMERIC(10,6),
    ema6_vs_ema26     NUMERIC(10,6),
    ema12_vs_ema26    NUMERIC(10,6),

    -- 2 EMA slope columns (1st derivative per (sec_type, code) by date) +
    -- 2 EMA curvature columns — windows 6/12 only, direct diffs of the
    -- EMA line values.
    ema6_slope        NUMERIC(10,6),
    ema12_slope       NUMERIC(10,6),

    ema6_curvature    NUMERIC(10,6),
    ema12_curvature   NUMERIC(10,6),

    -- 3 rolling population σ columns — one per typical long window
    -- {6, 12, 26} for the Bollinger envelopes.
    std_5days         NUMERIC(10,6),
    std_12days        NUMERIC(10,6),
    std_26days        NUMERIC(10,6),

    CONSTRAINT pk_mov_ave_spreads_detail_ema PRIMARY KEY (code, sec_type, date)
) PARTITION BY HASH (code);

-- Native hash partitions (16) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p15
SELECT public.create_hash_partitions('analysis', 'mov_ave_spreads_detail_ema', 16);

COMMENT ON TABLE  analysis.mov_ave_spreads_detail_ema              IS 'EMA-spread detail (WIDE format), the TYPICAL EMA pair set (2026-10-26 rebase): one row per (code, sec_type, date) with 6 EMA gap columns (3 Price/EMA + 3 EMA/EMA over the typical spans {6,12,26} — 12/26 the classic MACD pair) + 2 EMA slope + 2 EMA curvature columns (windows 6/12, direct diffs of the EMA line) + 3 rolling σ columns (std_{5,12,26}days). sec_type ∈ {etf, index, stock, industry}. Source: stats.{etf,index,stock}_tech_stats.ema{6,12,26} (industry computed in-pipeline with the same conventions).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ema.sec_type     IS 'Security type as its static.label_dim (''sec_type'' domain) SMALLINT code: 0 = etf (ETF), 1 = index (CSI-style index), 2 = stock (individual equity), 3 = industry (composite industry).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ema.code         IS 'Ticker. ETFs bare 6-digit (e.g. "510050"); indices bare code (e.g. "000300"); stocks keep their exchange suffix (e.g. "600008.SS").';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ema.date         IS 'Business date (trading day).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ema.price_vs_ema6   IS '(price - ema6) / ema6 — signed fractional gap (NULL when either is NULL/invalid).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ema.price_vs_ema12  IS '(price - ema12) / ema12 — the classic MACD fast EMA gap.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ema.price_vs_ema26  IS '(price - ema26) / ema26 — the classic MACD slow EMA gap.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ema.ema6_vs_ema12  IS '(ema6 - ema12) / ema12.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ema.ema6_vs_ema26  IS '(ema6 - ema26) / ema26.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ema.ema12_vs_ema26 IS '(ema12 - ema26) / ema26 — the classic MACD line''s components as a fractional spread.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ema.ema6_slope        IS '1st derivative of the EMA6 LINE (EMA6[t] - EMA6[t-1]) per trading day — a direct diff of the EMA values, not an average of the close-price diff. NULL on the first date of each code.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ema.ema12_slope       IS '1st derivative of the EMA12 LINE (EMA12[t] - EMA12[t-1]) per trading day. NULL on the first date of each code.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ema.ema6_curvature    IS '2nd derivative of EMA6 (ema6_slope[t] - ema6_slope[t-1]). NULL on the first two dates of each code.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ema.ema12_curvature   IS '2nd derivative of EMA12 (ema12_slope[t] - ema12_slope[t-1]). NULL on the first two dates of each code.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ema.std_5days   IS 'Rolling population σ (ddof=0) of price over 5 trading days. Bollinger band width for the EMA6 envelope (EMA6 uses the 5-day σ as the closest available window). NULL until 5 consecutive rows. Same source data as analysis.mov_ave_spreads_detail.std_5days — populated from the parent pipeline so the EMA table is self-contained for Bollinger rendering without a JOIN back to the SMA detail table.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ema.std_12days  IS 'Rolling population σ (ddof=0) of price over 12 trading days. Bollinger band width for the EMA12 envelope. NULL until 12 consecutive rows. Computed by the parent pipeline''s compute_rolling_stds; exists only on this table.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ema.std_26days  IS 'Rolling population σ (ddof=0) of price over 26 trading days. Bollinger band width for the EMA26 envelope. NULL until 26 consecutive rows. Computed by the parent pipeline''s compute_rolling_stds; exists only on this table.';
