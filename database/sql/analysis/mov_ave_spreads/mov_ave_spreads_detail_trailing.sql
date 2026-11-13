-- ============================================================================
--  Table: analysis.mov_ave_spreads_detail_trailing
--    The MA-spread detail's TRAILING-STAT companion (split 2026-11-08 out
--    of the former wide analysis.mov_ave_spreads_detail, column data
--    copied — not recomputed).
--
--    One row per (sec_type, code, date) — the SAME grain and PK as
--    analysis.mov_ave_spreads_detail, joined to it on that PK — carrying
--    the 27 per-gap trailing stats over each gap's OWN trailing
--    252-trading-day window:
--      {gap}_pct_rank   — the gap's percentile rank (0..1) within the
--                         window (rolling rank, the current point
--                         included; min_periods=252)
--      {gap}_zscore     — the gap's z against the window's mean/std
--                         (ddof=1, min_periods=252)
--      {gap}_verdict    — the stretched band as its static.label_dim
--                         'spread_verdict' SMALLINT code (0=stretched_low
--                         1=neutral 2=stretched_high; the pct_rank's
--                         top/bottom decile at thresholds 0.90/0.10)
--    — the numbers study_kit.studies.spread_report used to recompute per
--    sandbox run, materialized so readers retrieve them without
--    computation (analysis.mov_ave_spread; 2026-11-08).
--
--  Shared conventions (sec_type handling, the NO-FK derived-data design,
--  rebuild semantics): see 01_mov_ave_spreads_detail.sql.
-- ============================================================================
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: Each security-day's per-gap own-history trailing stats — where today's MA-spread gap (长短均线间距) sits in its trailing-year distribution (percentile rank, z-score, stretched verdict).
-- tags: ma-spread, 均线乖离, trailing stats, 滚动分位, percentile rank, 历史分位, z-score, stretched, 超买超卖, mean reversion, 均值回归, 间距, 均线间距


DROP TABLE IF EXISTS analysis.mov_ave_spreads_detail_trailing CASCADE;

CREATE TABLE analysis.mov_ave_spreads_detail_trailing (
    sec_type        SMALLINT     NOT NULL,  -- label code (static.label_dim 'sec_type'): 0=etf 1=index 2=stock 3=industry
    code            TEXT         NOT NULL,
    date            DATE         NOT NULL,

    -- 9 trailing pct-rank columns: the gap's percentile rank (0..1)
    -- within its OWN trailing 252-trading-day window (rank includes the
    -- current point; min_periods=252 — NULL until the window is fully
    -- realized). Recorded build parameters: window 252, thresholds n/a.
    price_vs_ma5_pct_rank    NUMERIC(7,6),
    price_vs_ma20_pct_rank   NUMERIC(7,6),
    price_vs_ma60_pct_rank   NUMERIC(7,6),
    price_vs_ma120_pct_rank  NUMERIC(7,6),
    price_vs_ma252_pct_rank  NUMERIC(7,6),
    ma5_vs_ma20_pct_rank     NUMERIC(7,6),
    ma5_vs_ma60_pct_rank     NUMERIC(7,6),
    ma5_vs_ma120_pct_rank    NUMERIC(7,6),
    ma5_vs_ma252_pct_rank    NUMERIC(7,6),
    ma20_vs_ma60_pct_rank    NUMERIC(7,6),
    ma60_vs_ma120_pct_rank   NUMERIC(7,6),
    ma120_vs_ma252_pct_rank  NUMERIC(7,6),

    -- 12 trailing z-score columns: the gap's z against its trailing
    -- 252-trading-day mean/std (ddof=1). Recorded build parameters:
    -- window 252, min_periods=252, ddof=1.
    price_vs_ma5_zscore    NUMERIC(10,6),
    price_vs_ma20_zscore   NUMERIC(10,6),
    price_vs_ma60_zscore   NUMERIC(10,6),
    price_vs_ma120_zscore  NUMERIC(10,6),
    price_vs_ma252_zscore  NUMERIC(10,6),
    ma5_vs_ma20_zscore     NUMERIC(10,6),
    ma5_vs_ma60_zscore     NUMERIC(10,6),
    ma5_vs_ma120_zscore    NUMERIC(10,6),
    ma5_vs_ma252_zscore    NUMERIC(10,6),
    ma20_vs_ma60_zscore    NUMERIC(10,6),
    ma60_vs_ma120_zscore   NUMERIC(10,6),
    ma120_vs_ma252_zscore  NUMERIC(10,6),

    -- 12 stretched-verdict columns: the gap's band as its static.label_dim
    -- 'spread_verdict' SMALLINT code (0=stretched_low 1=neutral
    -- 2=stretched_high) — the trailing pct_rank's top/bottom decile.
    -- Recorded build parameters: window 252, rank thresholds
    -- high >= 0.90 / low <= 0.10. NULL while the window is not realized.
    price_vs_ma5_verdict    SMALLINT,
    price_vs_ma20_verdict   SMALLINT,
    price_vs_ma60_verdict   SMALLINT,
    price_vs_ma120_verdict  SMALLINT,
    price_vs_ma252_verdict  SMALLINT,
    ma5_vs_ma20_verdict     SMALLINT,
    ma5_vs_ma60_verdict     SMALLINT,
    ma5_vs_ma120_verdict    SMALLINT,
    ma5_vs_ma252_verdict    SMALLINT,
    ma20_vs_ma60_verdict    SMALLINT,
    ma60_vs_ma120_verdict   SMALLINT,
    ma120_vs_ma252_verdict  SMALLINT,

    CONSTRAINT pk_mov_ave_spreads_detail_trailing PRIMARY KEY (code, sec_type, date)
) PARTITION BY HASH (code);

-- Native hash partitions (16) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p15
SELECT public.create_hash_partitions('analysis', 'mov_ave_spreads_detail_trailing', 16);

COMMENT ON TABLE  analysis.mov_ave_spreads_detail_trailing              IS 'MA-spread detail trailing stats (SPLIT 2026-11-08 from the former wide mov_ave_spreads_detail, data copied): one row per (code, sec_type, date) with per-gap trailing-252d pct_rank / zscore / stretched verdict (spread_verdict SMALLINT code) for the 12 gaps whose values live in analysis.mov_ave_spreads_detail (same grain and PK — join on (sec_type, code, date)). sec_type ∈ {etf, index, stock, industry}.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_trailing.sec_type   IS 'Security type as its static.label_dim (''sec_type'' domain) SMALLINT code: 0 = etf (ETF), 1 = index (CSI-style index), 2 = stock (individual equity), 3 = industry (composite industry).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_trailing.code         IS 'Ticker. ETFs bare 6-digit (e.g. "510050"); indices bare code (e.g. "000300"); stocks keep their exchange suffix (e.g. "600008.SS").';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_trailing.date         IS 'Business date (trading day).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_trailing.price_vs_ma5_pct_rank IS 'Percentile rank (0..1) of the price_vs_ma5 gap within its OWN trailing 252-trading-day window (rolling rank, the current point included; min_periods=252). The house own-history anchoring: how stretched today''s gap is vs this security''s own past year. NULL until the trailing window is fully realized. Built by analysis.mov_ave_spread (2026-11-08), semantics identical to study_kit.studies.trailing_pct_rank.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_trailing.price_vs_ma20_pct_rank IS 'Trailing-252d percentile rank (0..1) of the price_vs_ma20 gap (see price_vs_ma5_pct_rank).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_trailing.price_vs_ma60_pct_rank IS 'Trailing-252d percentile rank (0..1) of the price_vs_ma60 gap (see price_vs_ma5_pct_rank).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_trailing.price_vs_ma120_pct_rank IS 'Trailing-252d percentile rank (0..1) of the price_vs_ma120 gap (see price_vs_ma5_pct_rank).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_trailing.price_vs_ma252_pct_rank IS 'Trailing-252d percentile rank (0..1) of the price_vs_ma252 gap (see price_vs_ma5_pct_rank).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_trailing.ma5_vs_ma20_pct_rank IS 'Trailing-252d percentile rank (0..1) of the ma5_vs_ma20 gap (see price_vs_ma5_pct_rank).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_trailing.ma5_vs_ma60_pct_rank IS 'Trailing-252d percentile rank (0..1) of the ma5_vs_ma60 gap (see price_vs_ma5_pct_rank).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_trailing.ma5_vs_ma120_pct_rank IS 'Trailing-252d percentile rank (0..1) of the ma5_vs_ma120 gap (see price_vs_ma5_pct_rank).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_trailing.ma5_vs_ma252_pct_rank IS 'Trailing-252d percentile rank (0..1) of the ma5_vs_ma252 gap (see price_vs_ma5_pct_rank).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_trailing.ma20_vs_ma60_pct_rank IS 'Trailing-252d percentile rank (0..1) of the ma20_vs_ma60 gap (see price_vs_ma5_pct_rank).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_trailing.ma60_vs_ma120_pct_rank IS 'Trailing-252d percentile rank (0..1) of the ma60_vs_ma120 gap (see price_vs_ma5_pct_rank).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_trailing.ma120_vs_ma252_pct_rank IS 'Trailing-252d percentile rank (0..1) of the ma120_vs_ma252 gap (see price_vs_ma5_pct_rank).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_trailing.price_vs_ma5_zscore IS 'Z-score of the price_vs_ma5 gap against its trailing-252-trading-day mean/std (ddof=1, min_periods=252). NULL until the trailing window is fully realized. Semantics identical to study_kit.studies.zscore.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_trailing.price_vs_ma20_zscore IS 'Trailing-252d z-score of the price_vs_ma20 gap (see price_vs_ma5_zscore).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_trailing.price_vs_ma60_zscore IS 'Trailing-252d z-score of the price_vs_ma60 gap (see price_vs_ma5_zscore).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_trailing.price_vs_ma120_zscore IS 'Trailing-252d z-score of the price_vs_ma120 gap (see price_vs_ma5_zscore).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_trailing.price_vs_ma252_zscore IS 'Trailing-252d z-score of the price_vs_ma252 gap (see price_vs_ma5_zscore).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_trailing.ma5_vs_ma20_zscore IS 'Trailing-252d z-score of the ma5_vs_ma20 gap (see price_vs_ma5_zscore).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_trailing.ma5_vs_ma60_zscore IS 'Trailing-252d z-score of the ma5_vs_ma60 gap (see price_vs_ma5_zscore).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_trailing.ma5_vs_ma120_zscore IS 'Trailing-252d z-score of the ma5_vs_ma120 gap (see price_vs_ma5_zscore).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_trailing.ma5_vs_ma252_zscore IS 'Trailing-252d z-score of the ma5_vs_ma252 gap (see price_vs_ma5_zscore).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_trailing.ma20_vs_ma60_zscore IS 'Trailing-252d z-score of the ma20_vs_ma60 gap (see price_vs_ma5_zscore).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_trailing.ma60_vs_ma120_zscore IS 'Trailing-252d z-score of the ma60_vs_ma120 gap (see price_vs_ma5_zscore).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_trailing.ma120_vs_ma252_zscore IS 'Trailing-252d z-score of the ma120_vs_ma252 gap (see price_vs_ma5_zscore).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_trailing.price_vs_ma5_verdict IS 'Stretched band of the price_vs_ma5 gap as its static.label_dim (''spread_verdict'' domain) SMALLINT code: 0 = stretched_low (trailing pct_rank <= 0.10), 1 = neutral, 2 = stretched_high (pct_rank >= 0.90). A spread in the trailing-window tails is STRETCHED — mean-reversion pressure builds with the stretch; deep-discount tails are where snapbacks live. NULL while the trailing-252d window is not realized. Recorded build parameters: window 252, thresholds 0.90/0.10. Semantics identical to study_kit.studies.spread_report.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_trailing.price_vs_ma20_verdict IS 'Stretched verdict of the price_vs_ma20 gap — static.label_dim ''spread_verdict'' code (see price_vs_ma5_verdict).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_trailing.price_vs_ma60_verdict IS 'Stretched verdict of the price_vs_ma60 gap — static.label_dim ''spread_verdict'' code (see price_vs_ma5_verdict).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_trailing.price_vs_ma120_verdict IS 'Stretched verdict of the price_vs_ma120 gap — static.label_dim ''spread_verdict'' code (see price_vs_ma5_verdict).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_trailing.price_vs_ma252_verdict IS 'Stretched verdict of the price_vs_ma252 gap — static.label_dim ''spread_verdict'' code (see price_vs_ma5_verdict).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_trailing.ma5_vs_ma20_verdict IS 'Stretched verdict of the ma5_vs_ma20 gap — static.label_dim ''spread_verdict'' code (see price_vs_ma5_verdict).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_trailing.ma5_vs_ma60_verdict IS 'Stretched verdict of the ma5_vs_ma60 gap — static.label_dim ''spread_verdict'' code (see price_vs_ma5_verdict).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_trailing.ma5_vs_ma120_verdict IS 'Stretched verdict of the ma5_vs_ma120 gap — static.label_dim ''spread_verdict'' code (see price_vs_ma5_verdict).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_trailing.ma5_vs_ma252_verdict IS 'Stretched verdict of the ma5_vs_ma252 gap — static.label_dim ''spread_verdict'' code (see price_vs_ma5_verdict).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_trailing.ma20_vs_ma60_verdict IS 'Stretched verdict of the ma20_vs_ma60 gap — static.label_dim ''spread_verdict'' code (see price_vs_ma5_verdict).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_trailing.ma60_vs_ma120_verdict IS 'Stretched verdict of the ma60_vs_ma120 gap — static.label_dim ''spread_verdict'' code (see price_vs_ma5_verdict).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_trailing.ma120_vs_ma252_verdict IS 'Stretched verdict of the ma120_vs_ma252 gap — static.label_dim ''spread_verdict'' code (see price_vs_ma5_verdict).';
