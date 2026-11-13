-- ============================================================================
--  Table: analysis.mov_ave_spreads_detail   (MA-spread CORE: one row per
--                                            asset+code+date, 9 gap columns
--                                            + derivatives + σ)
--
--  Supports multiple security types via the `sec_type` column
--  ('etf' | 'index' | 'stock' | 'industry'). ETF prices use COALESCE(stats.etf_adjustment.adj_close,
--  stats.etf_basic_stats.close); index prices use stats.index_basic_stats.close
--  (indices have no adjustment table); stock prices use stats.stock_basic_stats.close
--  (stock_adjustment / stock_tech_stats not yet available — stock rows will be
--  populated once those tables exist); industry (sec_type=3, code = industry_id)
--  prices use the REBASED-TO-100 composite OHLC of stats.industry_basic_stats
--  (pool 'all' slice: price = mean_close, open/high/low = mean_*), with the
--  MA/EMA columns computed by the pipeline itself over the composite series
--  (industry has no *_tech_stats table; gap ratios are scale-invariant so the
--  rebased composite is directly comparable). MAs come from the corresponding
--  *_tech_stats table (ma5 / ma20 / ma60 / ma120 / ma252).
--
--  9 gap pairs (canonical order):
--    5 Price-vs-MA pairs:  gap = (price - maX) / maX,  X ∈ {5,20,60,120, 252}
--    4 MA5-vs-MA pairs:    gap = (ma5  - maX) / maX,  X ∈ {20,60,120, 252}
--
--  The former WIDE table was SPLIT (2026-11-08) into three same-grain
--  companions — each keeps this table's PK (code, sec_type, date):
--    analysis.mov_ave_trading_amt              — the 20 trading-amount
--        liquidity-trend columns (the mirror here was removed; that table is
--        the single authoritative home, written by the pipeline's
--        trading-amt internal step in the same pass);
--    analysis.mov_ave_spreads_detail_trailing  — the 27 per-gap trailing
--        stats ({gap}_pct_rank / _zscore / _verdict over the trailing
--        252-trading-day window; the numbers study_kit.studies.spread_report
--        used to recompute per sandbox run, materialized so readers
--        retrieve them without computation).
--  The former *_ma255-named legacy columns (the pre-252 window
--  convention) were dropped with the split — no reader referenced them.
--
--  Repopulated incrementally by `python -m analysis.mov_ave_spread`
--  (missing dates only; --force truncates first).
--
--  NOTE: NO FOREIGN KEYS / NO CHECK CONSTRAINTS by design. These tables
--  hold derived data owned and rebuilt by `analysis.mov_ave_spread`, so
--  each keeps only its PRIMARY KEY for row identity / upserts. (An
--  earlier revision carried a polymorphic FK pattern — generated
--  etf_date / index_date / stock_date discriminator columns + 3 FKs to
--  stats.{etf,index,stock}_identity ON DELETE CASCADE + 3 partial
--  FK-support indexes per table — plus sec_type CHECK constraints;
--  removed to keep the schema simple and bulk inserts cheap.)
--  Consequence: deleting/truncating stats identity rows no longer
--  cascades into these tables — rebuild them with
--  `python -m analysis.mov_ave_spread` (or `--force`) instead.
-- ============================================================================

-- Drop any prior version so a re-run rebuilds the table from the CREATE
-- below (data is repopulated by the pipeline --force runs; the registry
-- row re-registers via the runtime upsert_analysis_identity).
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: How far each security's price and its short MAs sit from its longer moving averages, as nine gap ratios per day — the 长短均线间距 / alignment (多头排列/空头排列) store — with derivatives and rolling σ (trailing stats and trading-amount metrics live in the same-grain companions analysis.mov_ave_spreads_detail_trailing / analysis.mov_ave_trading_amt).
-- tags: ma-spread, 均线乖离, indicators, 技术指标, moving average, 均线, trend, 趋势, alignment, 排列, 多头排列, 空头排列, 间距, 均线间距, 长短均线, 均线排列, bollinger, 布林带, 带宽, 金叉, 死叉, golden cross, death cross, cross, 交叉

DROP TABLE IF EXISTS analysis.mov_ave_spreads_detail CASCADE;

-- ----------------------------------------------------------------------------
--  Table: analysis.mov_ave_spreads_detail  (MA-spread CORE)
--    One row per (sec_type, code, date) holding the 9 gap values, the
--    6 derivatives (price + ma5/20 × slope/curvature) and the 5 rolling
--    price σ columns — the chart/signal core the UI and analysis_signals
--    read.
--
--  Columns:
--    sec_type, code, date              — PK; identifies the asset universe
--                                        (etf vs index vs stock vs industry),
--                                        ticker, and date
--    price_vs_ma{5,20,60,120, 252}    — (price - maX) / maX
--    ma5_vs_ma{20,60,120, 252}        — (ma5  - maX) / maX
--    price/ma5/ma20 slope+curvature   — 1st/2nd derivatives of the series
--                                        lines (see the column comments)
--    std_{5,20,60,120, 252}days       — rolling population σ of price over
--                                        N trading days (Bollinger band
--                                        width). Same units as price.
--                                        NULL until N rows are available.
--
--  Gap values are stored as signed fractional ratios (e.g. 0.05 = +5%).
--  NULL when either the numerator or denominator is NULL or non-positive.
--  σ values are in price units (not price²) so NUMERIC(10,6) holds them
--  without overflow for any realistic ETF / index / stock price.
-- ----------------------------------------------------------------------------
CREATE TABLE analysis.mov_ave_spreads_detail (
    sec_type        SMALLINT     NOT NULL,  -- label code (static.label_dim 'sec_type'): 0=etf 1=index 2=stock 3=industry
    code              TEXT         NOT NULL,
    date              DATE         NOT NULL,

    -- 5 Price-vs-MA gap columns
    price_vs_ma5      NUMERIC(10,6),
    price_vs_ma20     NUMERIC(10,6),
    price_vs_ma60     NUMERIC(10,6),
    price_vs_ma120    NUMERIC(10,6),
    price_vs_ma252    NUMERIC(10,6),

    -- 4 MA5-vs-MA gap columns
    ma5_vs_ma20       NUMERIC(10,6),
    ma5_vs_ma60       NUMERIC(10,6),
    ma5_vs_ma120      NUMERIC(10,6),
    ma5_vs_ma252      NUMERIC(10,6),

    -- 3 senior MA-vs-MA gap columns (2026-11-09, the real-run round: the
    -- deep-agent studies recomputed the whole close>ma5>ma20>ma60>ma120>
    -- ma252 chain from raw MA columns because only its first two legs
    -- were stored) — with the 9 original pairs they complete the
    -- inter-MA chain so any 排列/alignment construction is derivable
    -- from stored signs.
    ma20_vs_ma60      NUMERIC(10,6),
    ma60_vs_ma120     NUMERIC(10,6),
    ma120_vs_ma252    NUMERIC(10,6),

    -- The alignment aggregate over the five canonical legs
    -- (price_vs_ma5, ma5_vs_ma20, ma20_vs_ma60, ma60_vs_ma120,
    -- ma120_vs_ma252): score = count of positive legs (0..5); state = 2
    -- when all five legs are positive (bullish 多头排列), 0 when all
    -- five are negative (bearish 空头排列), 1 otherwise (mixed 纠缠).
    -- NULL when ANY leg gap is NULL (early history — ma252 needs 252
    -- rows — so "insufficient data" never masquerades as a verdict).
    -- Materializes the score/trajectory deep-agent studies recomputed
    -- with their own warmup logic (the 2026-11-09 run).
    alignment_score   SMALLINT,
    alignment_state   SMALLINT,  -- static.label_dim 'ma_alignment' code: 0=bearish 1=mixed 2=bullish

    -- Derivatives of the price (1-day leg) and the 5/20-day MA legs
    -- (2026-09: after trimming 120/252 then 20/60, the 5/20-day columns
    -- were restored as DB-computed columns — slope = value[t] − value[t−1]
    -- (a DIRECT diff of the MA line, not an average of the close diff),
    -- curvature = that slope's diff; NULL on the first/first-two dates of
    -- each code). ma{60,120, 252} slopes are still computed in-memory by
    -- the pipeline as trading_amt_ratios denominators but not persisted.
    price_slope       NUMERIC(10,6), -- 1st derivative of price (1d leg)
    ma5_slope         NUMERIC(10,6), -- 1st derivative of MA5
    ma20_slope        NUMERIC(10,6), -- 1st derivative of MA20

    price_curvature   NUMERIC(10,6), -- 2nd derivative of price (1d leg)
    ma5_curvature     NUMERIC(10,6), -- 2nd derivative of MA5
    ma20_curvature    NUMERIC(10,6), -- 2nd derivative of MA20

    -- 5 rolling population σ columns (in price units, Bollinger band width).
    -- σ_N[t] = sqrt( mean( (price[t-N+1..t] - mean(price[t-N+1..t]))^2 ) )
    -- using ddof=0 (population std, the Bollinger convention). NULL until
    -- the rolling window is fully populated (N consecutive rows).
    std_5days         NUMERIC(10,6),
    std_20days        NUMERIC(10,6),
    std_60days        NUMERIC(10,6),
    std_120days       NUMERIC(10,6),
    std_252days       NUMERIC(10,6),

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
COMMENT ON TABLE  analysis.mov_ave_spreads_detail              IS 'MA-spread detail (CORE): one row per (code, sec_type, date) with the 12 gap_value columns (5 Price/MA + 4 MA5/MA + 3 senior inter-MA legs) + the alignment aggregate (score 0..5 + its ma_alignment state) + 6 derivative columns (price + ma5/20 × slope/curvature) + 5 rolling price σ columns. The trading-amount metrics live in analysis.mov_ave_trading_amt; the per-gap trailing-252d pct_rank / zscore / stretched-verdict stats live in analysis.mov_ave_spreads_detail_trailing. sec_type ∈ {etf, index, stock, industry}.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.sec_type   IS 'Security type as its static.label_dim (''sec_type'' domain) SMALLINT code: 0 = etf (ETF), 1 = index (CSI-style index), 2 = stock (individual equity), 3 = industry (composite industry).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.code         IS 'Ticker. ETFs bare 6-digit (e.g. "510050"); indices bare code (e.g. "000300"); stocks keep their exchange suffix (e.g. "600008.SS").';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.date         IS 'Business date (trading day).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.price_vs_ma5 IS '(price - ma5) / ma5 — signed fractional gap (NULL when either is NULL/invalid).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.price_vs_ma20 IS '(price - ma20) / ma20.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.price_vs_ma60 IS '(price - ma60) / ma60.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.price_vs_ma120 IS '(price - ma120) / ma120.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.price_vs_ma252 IS '(price - ma252) / ma252.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.ma5_vs_ma20  IS '(ma5 - ma20) / ma20.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.ma5_vs_ma60  IS '(ma5 - ma60) / ma60.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.ma5_vs_ma120 IS '(ma5 - ma120) / ma120.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.ma5_vs_ma252 IS '(ma5 - ma252) / ma252.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.ma20_vs_ma60 IS '(ma20 - ma60) / ma60 — the inter-MA chain leg the deep-agent alignment studies needed (2026-11-09).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.ma60_vs_ma120 IS '(ma60 - ma120) / ma120 — inter-MA chain leg.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.ma120_vs_ma252 IS '(ma120 - ma252) / ma252 — inter-MA chain leg.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.alignment_score IS 'Count of positive legs (0..5) among the five canonical alignment legs (price_vs_ma5, ma5_vs_ma20, ma20_vs_ma60, ma60_vs_ma120, ma120_vs_ma252); NULL when ANY leg is NULL (insufficient MA history — never a verdict). Full-bear history reads 0, full-bull 5.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.alignment_state IS 'Alignment verdict as its static.label_dim (''ma_alignment'' domain) SMALLINT code: 2 = bullish (多头排列, score 5), 0 = bearish (空头排列, score 0), 1 = mixed (纠缠, otherwise); NULL when alignment_score is NULL.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.price_slope     IS '1st derivative of price (price[t] - price[t-1]) per trading day. NULL on the first date of each code.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.ma5_slope       IS '1st derivative of the MA5 LINE (MA5[t] - MA5[t-1]) per trading day — a direct diff of the moving-average values, not an average of the close-price diff. NULL on the first date of each code.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.ma20_slope      IS '1st derivative of the MA20 LINE (MA20[t] - MA20[t-1]) per trading day. NULL on the first date of each code.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.price_curvature IS '2nd derivative of price (slope[t] - slope[t-1]). NULL on the first two dates of each code.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.ma5_curvature   IS '2nd derivative of MA5 (ma5_slope[t] - ma5_slope[t-1]). NULL on the first two dates of each code.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.ma20_curvature  IS '2nd derivative of MA20 (ma20_slope[t] - ma20_slope[t-1]). NULL on the first two dates of each code.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.std_5days   IS 'Rolling population σ (ddof=0) of price over 5 trading days. Bollinger band width for MA5 envelope. NULL until 5 consecutive rows.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.std_20days  IS 'Rolling population σ (ddof=0) of price over 20 trading days. Bollinger band width for MA20 envelope. NULL until 20 consecutive rows.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.std_60days  IS 'Rolling population σ (ddof=0) of price over 60 trading days. Bollinger band width for MA60 envelope. NULL until 60 consecutive rows.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.std_120days IS 'Rolling population σ (ddof=0) of price over 120 trading days. Bollinger band width for MA120 envelope. NULL until 120 consecutive rows.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.std_252days IS 'Rolling population σ (ddof=0) of price over 252 trading days. Bollinger band width for MA252 envelope. NULL until 252 consecutive rows.';
