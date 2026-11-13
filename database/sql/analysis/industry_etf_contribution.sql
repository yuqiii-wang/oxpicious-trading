-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: How much ETF turnover each industry's member indices attract per day.
-- tags: etf, turnover, 成交额, industry, 行业




-- ============================================================================
--  Industry ETF Contribution — per-(date, industry_id, pool_size) aggregate
--  ETF trading turnover, sourced from stats.index_exts.
--
--  Table: analysis.industry_etf_contribution
--    PK: (date, industry_id, pool_size)
--
--  AGGREGATION
--    industry_etf_trading_amount = SUM(total_etf_trading_amount) across member
--      indices in the industry (from stats.index_exts). Each member index's
--      total_etf_trading_amount is the aggregate ETF turnover tracking that
--      index (precomputed in stats.index_exts by builds.index exts phase;
--      formerly relayed via cross_stats.code_etf_trading_amount — that stored
--      copy was consolidated away 2026-09-07 as a verbatim duplicate). The SUM
--      is the total ETF-market turnover tracking ANY member index in this
--      industry on this date.
--
--      NOTE: an ETF that tracks multiple member indices in the SAME industry
--      would be counted once per tracked index. In practice most ETFs track
--      exactly ONE index (parent_index_code), so double-counting is rare.
--      This mirrors the existing stats.industry_basic_stats.total_trading_amount
--      pattern (SUM across member indices) and is consistent with the
--      "ETF contribution = total ETF activity tracking this industry" semantics.
--
--  POOL_SIZE
--    Same classification as builds.industry:
--      small = stock_num < 51, mid = 51-180, large = > 180, all = every member.
--    stock_num is looked up from the LATEST sec_composition snapshot per index
--    code (temporal extrapolation — same as builds.industry).
--
--  MA5
--    industry_etf_trading_amount_ma5 = 5-trading-day moving average of
--    industry_etf_trading_amount, computed in pandas (rolling(5).mean(),
--    min_periods=1) per (industry_id, pool_size) group. Smooths the noisy
--    daily ETF turnover so the UI can show a stable trend.
--
--  MA20
--    industry_etf_trading_amount_ma20 = 20-trading-day moving average of
--    industry_etf_trading_amount, computed in pandas (rolling(20).mean(),
--    min_periods=1) per (industry_id, pool_size) group. A longer-window
--    smoother than MA5 for the UI's "Trading Amt" MA selector.
--
--  COUNT
--    industry_etf_count = COUNT(DISTINCT member index) with non-NULL
--      code_etf_trading_amount in this slice on this date.
--
--  SOURCE
--    stats.index_exts (total_etf_trading_amount, one row per (code, date))
--    stats.sec_classification (industry_id per index code)
--    stats.sec_composition (stock_num → pool_size classification)
--
--  POPULATION
--    analysis.market_beta.etf_contribution (internal step
--    run_etf_contribution, invoked from __main__ after attributions).
--    Depends on stats.index_exts (builds.index exts phase) being populated
--    first. Truncate-then-recompute on every run.
--
--  Register in analysis.analysis_identity (name='industry_etf_contribution').
-- ============================================================================
DROP TABLE IF EXISTS analysis.industry_etf_contribution;
CREATE TABLE IF NOT EXISTS analysis.industry_etf_contribution (
    date                      DATE          NOT NULL,
    industry_id               TEXT          NOT NULL,
    pool_size                 SMALLINT      NOT NULL,  -- label code (static.label_dim 'pool_size'): 0=small 1=mid 2=large 3=all

    -- Display label (denormalized)
    industry_label            TEXT          NOT NULL DEFAULT '',

    -- Number of member indices with non-NULL total_etf_trading_amount
    -- contributing to this (date, industry_id, pool_size) slice.
    industry_etf_count         INTEGER,

    -- SUM of total_etf_trading_amount across member indices in this
    -- pool_size slice on this date (yuan). NULL when no member index has
    -- ETF trading amount data.
    industry_etf_trading_amount     NUMERIC(24,4),

    -- 5-trading-day moving average of industry_etf_trading_amount.
    -- Populated by analysis.market_beta.etf_contribution via
    -- pandas rolling(5).mean() per (industry_id, pool_size) group.
    industry_etf_trading_amount_ma5 NUMERIC(24,4),

    -- 20-trading-day moving average of industry_etf_trading_amount.
    -- Populated by analysis.market_beta.etf_contribution via
    -- pandas rolling(20).mean() per (industry_id, pool_size) group.
    industry_etf_trading_amount_ma20 NUMERIC(24,4),

    CONSTRAINT pk_industry_etf_contribution PRIMARY KEY (industry_id, date, pool_size),
    CONSTRAINT chk_industry_etf_contribution_pool
        CHECK (pool_size BETWEEN 0 AND 3)
) PARTITION BY HASH (industry_id);

-- Native hash partitions (8) keyed by industry_id
-- Native hash partitions (8) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('analysis', 'industry_etf_contribution', 8);

CREATE INDEX IF NOT EXISTS idx_industry_etf_contribution_industry_pool_date
    ON analysis.industry_etf_contribution (industry_id, pool_size, date);
CREATE INDEX IF NOT EXISTS idx_industry_etf_contribution_date_industry
    ON analysis.industry_etf_contribution (date, industry_id);

COMMENT ON TABLE  analysis.industry_etf_contribution                        IS 'Per-(industry_id, date, pool_size) aggregate ETF trading turnover. industry_etf_trading_amount = SUM(total_etf_trading_amount) across member indices from stats.index_exts. industry_etf_count = COUNT of member indices with non-NULL ETF amount. Each member index contributes its aggregate ETF turnover (precomputed in stats.index_exts by builds.index exts phase). pool_size: small (stock_num<51), mid (51-180), large (>180), all. Built by analysis.market_beta.etf_contribution (internal step, truncate-then-recompute). Depends on stats.index_exts being populated first.';
COMMENT ON COLUMN analysis.industry_etf_contribution.pool_size              IS 'Pool-size slice as its static.label_dim (''pool_size'' domain) SMALLINT code: 0 small (stock_num<51), 1 mid (51-180), 2 large (>180), 3 all (every member). Classification source: stats.sec_composition (LATEST snapshot per code).';
COMMENT ON COLUMN analysis.industry_etf_contribution.industry_etf_count     IS 'Number of distinct member indices with non-NULL total_etf_trading_amount contributing to this (date, industry_id, pool_size) slice on this date.';
COMMENT ON COLUMN analysis.industry_etf_contribution.industry_etf_trading_amount     IS 'SUM(total_etf_trading_amount) across member indices in this pool_size slice on this date. Source: stats.index_exts (total_etf_trading_amount = aggregate ETF turnover tracking the member index, built by builds.index exts phase). NULL when no member index has ETF trading amount data on this date.';
COMMENT ON COLUMN analysis.industry_etf_contribution.industry_etf_trading_amount_ma5 IS '5-trading-day moving average of industry_etf_trading_amount. Populated by analysis.market_beta.etf_contribution via pandas rolling(5).mean() per (industry_id, pool_size) group (min_periods=1). NULL when the underlying value is NULL for the entire trailing 5-day window.';
COMMENT ON COLUMN analysis.industry_etf_contribution.industry_etf_trading_amount_ma20 IS '20-trading-day moving average of industry_etf_trading_amount. Populated by analysis.market_beta.etf_contribution via pandas rolling(20).mean() per (industry_id, pool_size) group (min_periods=1). NULL when the underlying value is NULL for the entire trailing 20-day window. Longer-window smoother than MA5, exposed by the UI "Trading Amt" MA selector.';

-- ----------------------------------------------------------------------------
--  Register in analysis.analysis_identity
-- ----------------------------------------------------------------------------
INSERT INTO analysis.analysis_identity (name, detail_name, summary_name, last_run_datetime, description) VALUES
    ('industry_etf_contribution', 'industry_etf_contribution', NULL, NOW(),
     'Per-(date, industry_id, pool_size) aggregate ETF trading turnover. industry_etf_trading_amount = SUM(code_etf_trading_amount) across member indices from analysis.sec_alloc_perf_attribution (sec_type=''index''). industry_etf_count = COUNT of member indices with non-NULL ETF amount. pool_size: small (stock_num<51), mid (51-180), large (>180), all. industry_etf_trading_amount_ma5 = 5-day MA, industry_etf_trading_amount_ma20 = 20-day MA. Built by analysis.market_beta.etf_contribution (internal step, truncate-then-recompute). Depends on analysis.sec_alloc_perf_attribution being populated first.')
ON CONFLICT (name) DO UPDATE SET
    detail_name       = EXCLUDED.detail_name,
    summary_name      = EXCLUDED.summary_name,
    last_run_datetime = NOW(),
    description       = EXCLUDED.description;
