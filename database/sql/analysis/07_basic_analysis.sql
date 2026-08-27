-- ============================================================================
--  Table: analysis.basic_analysis_stats
--
--  Per-(date, code, sec_type) basic analysis flags for securities across all
--  supported security types ('etf' | 'index' | 'stock'). One row per trading
--  date per security, keyed identically to analysis.mov_ave_spreads_detail so
--  the two tables can be joined on (sec_type, code, date) without remapping.
--
--  PK: (date, code, sec_type)
--
--  Columns:
--    date              — trading date
--    code              — security code (etf / index / stock ticker)
--    sec_type          — 'etf' | 'index' | 'stock'
--    is_market_hyped   — boolean flag indicating whether the security is
--                        considered "market hyped" on this date (abnormal
--                        market attention / activity). FALSE by default;
--                        set to TRUE by the populating build script when the
--                        security trips the hype criteria for that date.
--
--  POPULATION
--    Populated by the corresponding analyze build script (truncate-then-
--    recompute on every run, mirroring the other analysis tables).
--
--  Register in analysis.analysis_identity (name='basic_analysis_stats').
-- ============================================================================
CREATE TABLE IF NOT EXISTS analysis.basic_analysis_stats (
    date              DATE         NOT NULL,
    code              TEXT         NOT NULL,
    sec_type          TEXT         NOT NULL,  -- 'etf' | 'index' | 'stock'

    -- Boolean flag: TRUE when the security is considered "market hyped" on
    -- this date (abnormal market attention / activity). FALSE by default.
    is_market_hyped   BOOLEAN      NOT NULL DEFAULT FALSE,

    CONSTRAINT pk_basic_analysis_stats PRIMARY KEY (code, date, sec_type),
    CONSTRAINT chk_basic_analysis_stats_sec_type
        CHECK (sec_type IN ('stock', 'etf', 'index'))
) PARTITION BY HASH (code);

-- Native hash partitions (8) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('analysis', 'basic_analysis_stats', 8);

-- Indexes for the common access patterns:
--   1. Per-security time series (drives per-code charts).
--   2. Per-date snapshot (drives the latest-date hyped-securities list).
CREATE INDEX IF NOT EXISTS idx_basic_analysis_stats_code_sec_type_date
    ON analysis.basic_analysis_stats (code, sec_type, date);
CREATE INDEX IF NOT EXISTS idx_basic_analysis_stats_date
    ON analysis.basic_analysis_stats (date);

COMMENT ON TABLE  analysis.basic_analysis_stats              IS 'Per-(date, code, sec_type) basic analysis flags across all security types (etf / index / stock). One row per trading date per security. PK: (code, date, sec_type) — joins 1:1 with analysis.mov_ave_spreads_detail on the same key. is_market_hyped: boolean flag set by the build script when the security trips the hype criteria on that date. Built by the corresponding analyze build script (truncate-then-recompute).';
COMMENT ON COLUMN analysis.basic_analysis_stats.date        IS 'Trading date.';
COMMENT ON COLUMN analysis.basic_analysis_stats.code        IS 'Security code (etf / index / stock ticker).';
COMMENT ON COLUMN analysis.basic_analysis_stats.sec_type    IS 'Subject security type: stock, etf, or index. Determines which source price/valuation tables apply (mirrors analysis.mov_ave_spreads_detail.sec_type).';
COMMENT ON COLUMN analysis.basic_analysis_stats.is_market_hyped IS 'Boolean flag: TRUE when the security is considered "market hyped" on this date (abnormal market attention / activity). FALSE by default; flipped to TRUE by the populating build script when the hype criteria are met for this (date, code, sec_type).';

-- ----------------------------------------------------------------------------
--  Register in analysis.analysis_identity
-- ----------------------------------------------------------------------------
INSERT INTO analysis.analysis_identity (name, detail_name, summary_name, last_run_datetime, description) VALUES
    ('basic_analysis_stats', NULL, NULL, NOW(),
     'Per-(date, code, sec_type) basic analysis flags across all security types (etf / index / stock). One row per trading date per security. PK: (date, code, sec_type). is_market_hyped: boolean flag set by the build script when the security trips the hype criteria on that date. Built by the corresponding analyze build script (truncate-then-recompute).')
ON CONFLICT (name) DO UPDATE SET
    detail_name       = EXCLUDED.detail_name,
    summary_name      = EXCLUDED.summary_name,
    last_run_datetime = NOW(),
    description       = EXCLUDED.description;
