-- ============================================================================
--  Market Beta — derived tables built on top of stats.industry_basic_stats
--  (the former analysis.industry_sentiments table — DDL now lives in
--  database/sql/stats/13_industry.sql — migrated 2026-08-24).
--
--  Contains:
--    • analysis.industry_correlations  (rolling pairwise Pearson correlation
--      of the MEAN rebased-to-100 price series between two industries)
--    • analysis.industry_attributions  (composition overlap between each
--      industry and each benchmark index)
--    • analysis.industry_etf_contribution (per-(date, industry_id, pool_size)
--      aggregate ETF trading turnover)
--
--  POPULATION
--    builds/industry (stats.industry_basic_stats: incremental /
--    --force truncate-then-recompute) then analysis/market_beta
--    (downstream steps, truncate-then-recompute on every run).
-- ============================================================================

-- ============================================================================
--  Industry Correlations — windowed Pearson correlation of the industries'
--  MA curves, bucketed by pool_size.
--
--  Table: analysis.industry_correlations
--    PK: (industry_id, benchmark_industry_id, pool_size, start_date, interval)
--
--  SOURCE
--    stats.industry_basic_stats.mean_close     (per-industry composite close
--    series — former mean_price, rehooked 2026-08-24)
--
--  WINDOW SEMANTICS (corr_ma{W}_{W}d, W in {20, 60, 252})
--    For each industry, the MA-W curve is the trailing W-trading-day rolling
--    mean of mean_close. Windows start on the pool calendar grid: start
--    indices 0, interval, 2*interval, ... (interval defaults to 20 trading
--    days — the stride between consecutive compute windows). The window for
--    corr_ma{W}_{W}d spans the W trading days [start_date, start_date + W).
--    The stored value is the Pearson correlation between the two industries'
--    MA-W curves over those W dates. Only FULL windows (all W dates present
--    in the calendar, both industries' MA-W defined on every date of the
--    window) are materialized; otherwise the column is NULL.
--
--    A window's value is final once its last date exists, so rows are
--    emitted exactly when start_date + W - 1 first appears in the source.
--
--  CONVENTION
--    For each pair of industries (A, B) and each pool_size slice P, rows
--    are keyed by the window START date. Both industries are compared in
--    the SAME pool_size slice — a single `pool_size` column captures this.
--
--    Cross-pool comparisons (e.g. corr(A.small_mean, B.large_mean)) are NOT
--    materialized — they conflate cross-index size effects with sentiment
--    co-movement and are not meaningful for industry-vs-industry analysis.
--    Only the 4 same-pool slices (all, small, mid, large) are populated.
--
--    Self-pairs (A = B) are NOT materialized — self-correlation is always 1.
--
--    Order convention: rows are stored with industry_id < benchmark_industry_id
--    (lexicographic) to deduplicate (A,B) vs (B,A). The API returns rows
--    matching either direction of the user-selected industry_ids set.
--
--  POPULATION
--    analysis.market_beta.correlations (internal step
--    run_correlations, invoked from __main__ — incremental / force).
-- ============================================================================
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: How two composite industries' price trends co-move over rolling audit windows.
-- tags: industry, 行业, correlation, 相关性, market beta, 市场贝塔

DROP TABLE IF EXISTS analysis.industry_correlations;

CREATE TABLE IF NOT EXISTS analysis.industry_correlations (
    industry_id                       TEXT          NOT NULL,
    benchmark_industry_id             TEXT          NOT NULL,
    pool_size                         SMALLINT      NOT NULL,  -- label code (static.label_dim 'pool_size'): 0=small 1=mid 2=large 3=all
    start_date                        DATE          NOT NULL,
    interval                          INTEGER       NOT NULL DEFAULT 20,

    -- Pearson correlation between the two industries' MA-{W} curves over
    -- the {W}-trading-day window starting on start_date. NULL when the
    -- window is not full or either industry's MA-W is undefined on any of
    -- its dates.
    corr_ma20_20d                     NUMERIC(8,4),
    corr_ma60_60d                     NUMERIC(8,4),
    corr_ma252_252d                   NUMERIC(8,4),

    -- No CHECK constraints (pool/interval enums + pair ordering): the
    -- builder guarantees these; per-row validation only slowed the bulk
    -- COPY writes. No secondary indexes either — the PK serves the
    -- per-pair time-series chart; reverse-direction lookups scan the
    -- PK (757K rows).
    CONSTRAINT pk_industry_correlations PRIMARY KEY
        (industry_id, benchmark_industry_id, pool_size, start_date, interval)
) PARTITION BY HASH (industry_id);

-- Native hash partitions (32) keyed by industry_id
-- Native hash partitions (32) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p31
SELECT public.create_hash_partitions('analysis', 'industry_correlations', 32);

-- Indexes: none beyond the PK (post-indexing not needed — per-pair chart
-- queries are PK-prefix scans).

COMMENT ON TABLE  analysis.industry_correlations                            IS 'Windowed pairwise Pearson correlation between two industries'' MA curves of mean_close (stats.industry_basic_stats.mean_close, built by builds.industry — former mean_price). One row per (industry_id, benchmark_industry_id, pool_size, start_date, interval). Windows start on the pool calendar grid every `interval` (default 20) trading days; corr_ma{W}_{W}d (W in 20/60/252) correlates the two industries'' MA-W curves over the W trading days starting on start_date. Both industries are compared in the SAME pool_size slice (single pool_size column). Self-pairs (A=B) excluded. Order convention: industry_id < benchmark_industry_id (lexicographic) to deduplicate (A,B) vs (B,A). Only same-pool slices materialized (all, small, mid, large). Built by analysis.market_beta.correlations (internal step, incremental / force).';
COMMENT ON COLUMN analysis.industry_correlations.industry_id                IS 'Subject industry''s industry_id (lexicographically smaller of the two).';
COMMENT ON COLUMN analysis.industry_correlations.benchmark_industry_id       IS 'Benchmark industry''s industry_id (lexicographically larger of the two).';
COMMENT ON COLUMN analysis.industry_correlations.pool_size                  IS 'Pool_size slice in which BOTH industries are compared (cross-pool comparisons are not materialized), as its static.label_dim (''pool_size'' domain) SMALLINT code: 0 small (stock_num<51), 1 mid (51-180), 2 large (>180), 3 all (every member).';
COMMENT ON COLUMN analysis.industry_correlations.start_date                 IS 'Start date of the compute window on the pool calendar grid (grid stride = interval trading days). The window for corr_ma{W}_{W}d spans [start_date, start_date + W).';
COMMENT ON COLUMN analysis.industry_correlations."interval"                 IS 'Stride in trading days between consecutive window starts on the pool calendar grid (default 20).';
COMMENT ON COLUMN analysis.industry_correlations.corr_ma20_20d              IS 'Pearson correlation between the two industries'' MA20 curves over the 20 trading days starting on start_date. NULL when the window is not full or either MA20 curve is undefined on any of its dates.';
COMMENT ON COLUMN analysis.industry_correlations.corr_ma60_60d              IS 'Pearson correlation between the two industries'' MA60 curves over the 60 trading days starting on start_date. NULL when the window is not full or either MA60 curve is undefined on any of its dates.';
COMMENT ON COLUMN analysis.industry_correlations.corr_ma252_252d            IS 'Pearson correlation between the two industries'' MA252 curves over the 252 trading days starting on start_date. NULL when the window is not full or either MA252 curve is undefined on any of its dates.';
