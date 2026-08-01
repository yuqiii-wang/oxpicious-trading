-- ============================================================================
--  Capital Flow Analysis — industry-based ETF flow with broad-market effect
--  removed, capturing trending popularity of each industry.
--
--  Table: analysis.capital_flow
--    One row per (date, industry_id, benchmark_code) where:
--      • industry_id    = L2 industry classification (BANKS, SEMI, PHARMA_BROAD,
--                        BROAD_CSI, DEBT_CORP, ...) — same namespace as
--                        stats.sec_classification.industry_id and the `code`
--                        column of stats.etf_trading_amt.
--      • benchmark_code = a broad-market index code (000300 沪深300, 000852
--                        中证1000, 000001 上证指数, ...) sourced from
--                        stats.sec_index_tags WHERE is_broad_market = TRUE.
--
--  MODEL (overlap-weighted, confirmed by user):
--    Given:
--      I  = industry ETF trading amount (stats.etf_trading_amt.total_etf_amt
--           where code = industry_id)
--      B  = benchmark ETF trading amount (stats.index_exts.total_etf_amt
--           where code = benchmark_code)
--      w_i = fraction of INDUSTRY weight on overlap stocks (held by BOTH
--            industry's representative index and the benchmark) — from latest
--            stats.sec_composition snapshot. Stored as percent in DB; divided
--            by 100 in Python before computation.
--      w_b = fraction of BENCHMARK weight on overlap stocks.
--      O_i = I * w_i     (industry-side overlap trading)
--      O_b = B * w_b     (benchmark-side overlap trading)
--      g_i = industry daily return (weighted avg of ETF returns in the
--            industry, weighted by amount_wan)
--      g_b = benchmark daily return (index_basic_stats.close diff)
--
--    Pure metrics (broad-market effect removed):
--      pure_flow         = I * (1 - w_i * O_b / (O_b + O_i))
--                          — the industry's genuine trading after stripping
--                            the broad-market-driven portion of its overlap
--                            trading. Proportional attribution: the overlap
--                            stocks' trading is split between industry and
--                            benchmark by their respective overlap shares.
--      pure_growth       = g_i - w_i * g_b
--                          — overlap-weighted alpha. Strips the fraction of
--                            industry growth attributable to broad-market
--                            beta (only the w_i overlap portion moves with
--                            the broad market).
--      pure_popularity   = pure_flow * pure_growth
--                          — combined size-and-momentum metric.
--      observed_popularity = I * g_i
--                          — raw popularity (no broad-market removal) for
--                            comparison.
--      popularity_retention = pure_popularity / observed_popularity
--                          — ratio in [0, 1+]; <1 means the industry's
--                            actual popularity is less than observed.
--
--    Example (B=1000mil, I=100mil, w_b=10%, w_i=60%, g_b=2%, g_i=5%):
--      O_b=100, O_i=60, O_b/(O_b+O_i)=0.625
--      pure_flow = 100 * (1 - 0.6*0.625) = 62.5 mil
--      pure_growth = 5% - 0.6*2% = 3.8%
--      pure_popularity = 62.5 * 3.8% = 2.375 (47.5% of observed 5.0)
--
--  Overlap weights (w_i, w_b) are CONSTANT across all dates for one
--  (industry, benchmark) pair — sourced from the LATEST snapshot in
--  stats.sec_composition. The industry's composition is proxied by its
--  REPRESENTATIVE INDEX: the index in that industry with the highest
--  total ETF tracking amount (stats.index_exts.total_etf_amt). This is a
--  deliberate simplification — an industry may have many indices with
--  overlapping stock universes, and aggregating their compositions would
--  double-count stocks. The representative index is the single most-liquid
--  proxy for the industry's stock universe.
--
--  Source: analyze_capital_flow.py (truncate-then-recompute on every run).
-- ============================================================================

-- ----------------------------------------------------------------------------
--  Table: analysis.capital_flow
--  PK: (date, industry_id, benchmark_code)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS analysis.capital_flow (
    date                      DATE          NOT NULL,
    industry_id               TEXT          NOT NULL,
    benchmark_code            TEXT          NOT NULL,

    -- Display labels (denormalized)
    industry_label            TEXT          NOT NULL DEFAULT '',
    benchmark_label           TEXT          NOT NULL DEFAULT '',

    -- Industry (subject) observed values
    industry_etf_amount       NUMERIC(18,4),   -- I (yuan): Σ ETF amount tracking indices in this industry
    industry_etf_num          INTEGER,         -- count of ETFs in this industry on this date
    industry_return           NUMERIC(10,6),   -- g_i (fractional daily return, weighted avg of ETF returns)

    -- Benchmark (broad market) observed values
    benchmark_etf_amount      NUMERIC(18,4),   -- B (yuan): Σ ETF amount tracking the benchmark index
    benchmark_etf_num         INTEGER,         -- count of ETFs tracking the benchmark on this date
    benchmark_return          NUMERIC(10,6),   -- g_b (fractional daily return = close diff / prev close)

    -- Overlap (constant per pair, from latest sec_composition snapshot)
    -- Stored as PERCENT (0-100) to match sec_composition.weight_pct convention.
    -- Divided by 100 in Python when computing pure_* metrics.
    industry_overlap_weight   NUMERIC(10,6),   -- w_i: Σ industry-index weight on overlap stocks (%)
    benchmark_overlap_weight  NUMERIC(10,6),   -- w_b: Σ benchmark weight on overlap stocks (%)
    industry_overlap_amount   NUMERIC(18,4),   -- O_i = I * w_i / 100 (yuan)
    benchmark_overlap_amount  NUMERIC(18,4),   -- O_b = B * w_b / 100 (yuan)

    -- Pure (broad-market-removed) metrics — the analysis output
    pure_flow                 NUMERIC(18,4),   -- I * (1 - w_i * O_b / (O_b + O_i))  (yuan)
    pure_growth               NUMERIC(10,6),   -- g_i - w_i * g_b  (fractional)
    pure_popularity           NUMERIC(20,6),   -- pure_flow * pure_growth
    observed_popularity       NUMERIC(20,6),   -- I * g_i  (raw, for comparison)
    popularity_retention      NUMERIC(20,6),   -- pure_popularity / observed_popularity (unbounded ratio)

    CONSTRAINT pk_capital_flow PRIMARY KEY (date, industry_id, benchmark_code)
);

-- Indexes for the common access patterns:
--   1. Per-industry time series (drives the chart on the CapitalFlow page).
--   2. Per-date snapshot (drives the latest-date codes list).
--   3. Per-benchmark filter (when user selects a specific broad-market benchmark).
CREATE INDEX IF NOT EXISTS idx_capital_flow_industry_date
    ON analysis.capital_flow (industry_id, date);
CREATE INDEX IF NOT EXISTS idx_capital_flow_date_industry
    ON analysis.capital_flow (date, industry_id);
CREATE INDEX IF NOT EXISTS idx_capital_flow_benchmark
    ON analysis.capital_flow (benchmark_code, date);

COMMENT ON TABLE  analysis.capital_flow                          IS 'Industry-based ETF capital flow with broad-market effect removed. One row per (date, industry_id, benchmark_code). Pure metrics (pure_flow, pure_growth, pure_popularity) strip the broad-market spillover from the industry''s observed ETF trading and return using overlap-weighted proportional attribution. popularity_retention = pure/observed (<1 means actual popularity is less than observed).';
COMMENT ON COLUMN analysis.capital_flow.industry_id              IS 'L2 industry classification id (BANKS, SEMI, PHARMA_BROAD, BROAD_CSI, ...). Matches stats.sec_classification.industry_id and stats.etf_trading_amt.code.';
COMMENT ON COLUMN analysis.capital_flow.benchmark_code           IS 'Broad-market index code (000300, 000852, 000001, ...). Sourced from stats.sec_index_tags WHERE is_broad_market = TRUE.';
COMMENT ON COLUMN analysis.capital_flow.industry_etf_amount       IS 'I (yuan): aggregate ETF trading turnover on this date across ALL ETFs tracking indices in this industry. Source: stats.etf_trading_amt.total_etf_amt where code = industry_id.';
COMMENT ON COLUMN analysis.capital_flow.industry_etf_num         IS 'Number of ETFs whose linked parent index carries this industry_id on this date.';
COMMENT ON COLUMN analysis.capital_flow.industry_return           IS 'g_i: industry daily return (fractional). Weighted average of all ETF returns in this industry, weighted by amount_wan. First date per industry has NULL (no prior close).';
COMMENT ON COLUMN analysis.capital_flow.benchmark_etf_amount     IS 'B (yuan): aggregate ETF trading turnover tracking the benchmark index on this date. Source: stats.index_exts.total_etf_amt where code = benchmark_code. NULL when no ETF tracks the benchmark.';
COMMENT ON COLUMN analysis.capital_flow.benchmark_etf_num        IS 'Number of ETFs tracking the benchmark on this date.';
COMMENT ON COLUMN analysis.capital_flow.benchmark_return         IS 'g_b: benchmark daily return (fractional = (close_t - close_{t-1}) / close_{t-1}). Source: stats.index_basic_stats.close diff.';
COMMENT ON COLUMN analysis.capital_flow.industry_overlap_weight  IS 'w_i (PERCENT): fraction of industry weight on stocks held by BOTH the industry''s representative index and the benchmark. Constant per (industry, benchmark) pair — sourced from latest stats.sec_composition snapshot. Industry composition proxied by its representative index (highest total_etf_amt).';
COMMENT ON COLUMN analysis.capital_flow.benchmark_overlap_weight IS 'w_b (PERCENT): fraction of benchmark weight on overlap stocks. Constant per pair.';
COMMENT ON COLUMN analysis.capital_flow.industry_overlap_amount  IS 'O_i = I * w_i / 100 (yuan). Industry-side trading in overlap stocks.';
COMMENT ON COLUMN analysis.capital_flow.benchmark_overlap_amount IS 'O_b = B * w_b / 100 (yuan). Benchmark-side trading in overlap stocks.';
COMMENT ON COLUMN analysis.capital_flow.pure_flow                IS 'I * (1 - w_i * O_b / (O_b + O_i)) (yuan). Industry''s genuine trading after removing the broad-market-driven portion of its overlap trading. Proportional attribution: overlap trading split by respective overlap shares. Falls back to I when O_b + O_i = 0 (no overlap).';
COMMENT ON COLUMN analysis.capital_flow.pure_growth              IS 'g_i - w_i * g_b (fractional). Overlap-weighted alpha: strips the fraction of industry growth attributable to broad-market beta (only the w_i overlap portion moves with the broad market).';
COMMENT ON COLUMN analysis.capital_flow.pure_popularity          IS 'pure_flow * pure_growth. Combined size-and-momentum metric for industry-specific popularity.';
COMMENT ON COLUMN analysis.capital_flow.observed_popularity      IS 'I * g_i. Raw popularity (no broad-market removal) for comparison with pure_popularity.';
COMMENT ON COLUMN analysis.capital_flow.popularity_retention     IS 'pure_popularity / observed_popularity. Ratio in [0, +inf) — <1 means actual popularity is less than observed (broad market was inflating it). >1 is possible when pure_growth > observed growth (broad market was a drag, e.g. industry rose while broad market fell). NULL when observed_popularity is 0/NULL.';

-- ----------------------------------------------------------------------------
--  Register in analysis.analysis_identity
-- ----------------------------------------------------------------------------
INSERT INTO analysis.analysis_identity (name, detail_name, summary_name, last_run_datetime, description) VALUES
    ('capital_flow', 'capital_flow', NULL, NOW(),
     'Industry-based ETF capital flow with broad-market effect removed. One row per (date, industry_id, benchmark_code) where benchmark_code is a broad-market index (from stats.sec_index_tags is_broad_market=TRUE). Computes overlap-weighted pure metrics: pure_flow = I*(1 - w_i*O_b/(O_b+O_i)) strips broad-market-driven overlap trading; pure_growth = g_i - w_i*g_b strips broad-market beta from industry growth; pure_popularity = pure_flow * pure_growth. observed_popularity = I*g_i for comparison; popularity_retention = pure/observed. Overlap weights (w_i, w_b) are constant per pair from latest stats.sec_composition snapshot (industry composition proxied by representative index = highest total_etf_amt). Example: B=1000mil, I=100mil, w_b=10%, w_i=60%, g_b=2%, g_i=5% → pure_flow=62.5mil, pure_growth=3.8%, pure_popularity=2.375 (47.5% of observed 5.0).')
ON CONFLICT (name) DO UPDATE SET
    detail_name       = EXCLUDED.detail_name,
    summary_name      = EXCLUDED.summary_name,
    last_run_datetime = NOW(),
    description       = EXCLUDED.description;
