-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: How much composition overlap each industry shares with each benchmark index, and the non-shared remainder.
-- tags: industry, 行业, attribution, 归因, composition overlap, 持仓重合, shared weights, 共享权重, benchmark, 基准




-- ============================================================================
--  Industry Attributions — composition overlap between each industry (as a
--  group of member indices) and each benchmark index, aggregated to the
--  industry level.
--
--  Table: analysis.industry_attributions
--    PK: (date, industry_id, benchmark_code, attribution_type)
--
--  TWO ATTRIBUTION VARIANTS (attribution_type column)
--    'trading_amt' — industry_shared_weight = SUM(code_sec_shared_weight)
--                    across member indices (current behavior; can exceed 100).
--    'equal'       — industry_shared_weight = AVG(code_sec_shared_weight) =
--                    SUM / N (N = number of active member indices in the
--                    industry from stats.sec_classification). Each member
--                    gets an equal share of the total overlap weight.
--
--    benchmark_shared_weight is UNDIVIDED (same for both variants) — it is a
--    property of the benchmark's composition on the industry's stock union,
--    NOT of the industry's member count. Consequently the swf =
--    benchmark_shared_weight / 100 is identical for both variants, so ALL
--    non_this_industry_* columns (price, rolling_*, trading_amt) are IDENTICAL
--    between 'equal' and 'trading_amt'. The ONLY difference is the
--    industry_shared_weight column.
--
--  HYBRID AGGREGATION (avoids double-counting; see decision log)
--    industry_shared_weight  = SUM(code_sec_shared_weight) across member
--                              indices in the industry, sourced from
--                              analysis.sec_alloc_perf_attribution (sec_type
--                              ='index'). Each member index contributes its
--                              OWN weight on stocks shared with the benchmark,
--                              so the sum is a clean "total member overlap"
--                              (can exceed 100 when summing multiple member
--                              portfolios — expected, NOT double-counting).
--                              Self-pairs (member == benchmark) are already
--                              excluded by sec_alloc_perf_attribution, so the
--                              benchmark's own overlap with itself is NOT
--                              counted here.
--
--    benchmark_shared_weight = benchmark's weight on the UNION of stocks held
--                              by ANY industry member (latest sec_composition
--                              snapshot, source_type='index'). Each stock
--                              counted ONCE (union), so no double-counting
--                              even when multiple members hold the same stock.
--                              Bounded [0, 100] (weight_pct is stored as a percent, 0-100, not a fraction): the fraction of the benchmark's
--                              weight that lies in the industry's stock union.
--                              Recomputed from sec_composition (NOT summed
--                              from sec_alloc_perf_attribution) because a
--                              naive SUM of benchmark_sec_shared_weight across
--                              members would double-count stocks held by
--                              multiple members (the benchmark's weight on
--                              such a stock would be added once per member).
--
--  TEMPORAL CONVENTION
--    Both columns use the LATEST sec_composition snapshot per code for ALL
--    dates (temporal extrapolation — same as sec_alloc_perf_attribution and
--    market_beta). The shared-weight VALUES are constant per
--    (industry_id, benchmark_code) across dates; the `date` dimension is
--    inherited from sec_alloc_perf_attribution (dates where at least one
--    member index has a row for the benchmark). Dates where a member index
--    starts later mechanically reduce the industry_shared_weight sum before
--    that member's history begins, so the sum can vary by date when member
--    indices have unequal history lengths.
--
--  EDGE CASE: benchmark is itself a member of the industry
--    industry_shared_weight excludes the benchmark's self-pair (see above),
--    so it measures the OTHER members' overlap with the benchmark.
--    benchmark_shared_weight, however, includes the benchmark's own stocks
--    in the industry union, so it can be close to 100 (the benchmark is
--    fully contained in its own industry). This is a true reflection of
--    "the benchmark's weight in the industry's stocks" and is documented
--    here for clarity. It mainly affects non-broad benchmarks (broad
--    indices like 000300 are in BROAD_* sectors, not industry sectors).
--
--  SOURCE
--    analysis.sec_alloc_perf_attribution  (code_sec_shared_weight, per
--                                          (code, benchmark_code, date))
--    stats.sec_classification              (industry_id per index code)
--    stats.sec_composition                 (holdings -> union of industry
--                                          member stocks + benchmark weights)
--
--  POPULATION
--    analysis.market_beta.attributions (internal step
--    run_attributions, invoked from __main__ after correlations). Depends on
--    analysis.sec_alloc_perf_attribution being populated first (by
--    analysis.sec_alloc_perf_attribution). Truncate-then-recompute on every run.
--
--  Register in analysis.analysis_identity (name='industry_attributions').
-- ============================================================================
DROP TABLE IF EXISTS analysis.industry_attributions;
CREATE TABLE IF NOT EXISTS analysis.industry_attributions (
    industry_id                       TEXT          NOT NULL,
    benchmark_code                    TEXT          NOT NULL,
    date                              DATE          NOT NULL,

     -- 'equal' or 'trading_amt'. For 'trading_amt' (default), industry_shared_weight
     -- = SUM(code_sec_shared_weight) across member indices (can exceed 100). For
     -- 'equal', industry_shared_weight = AVG = SUM / N (N = active member index
     -- count from stats.sec_classification). benchmark_shared_weight is UNDIVIDED
     -- (same for both variants — it is a property of the benchmark's composition,
     -- not the industry's member count). Consequently ALL non_this_industry_*
     -- columns are IDENTICAL between the two variants.
    attribution_type                  TEXT          NOT NULL,


    -- SUM w_subject on shared stocks, summed across member indices in the
    -- industry (from analysis.sec_alloc_perf_attribution.code_sec_shared_weight).
    -- Can exceed 100 (sum of multiple member portfolios). NULL when the
    -- benchmark has no composition data.
    industry_shared_weight         NUMERIC(8,4),

    -- SUM w_benchmark on the UNION of industry member stocks (latest
    -- sec_composition snapshot). Bounded [0, 100] (weight_pct is stored as a percent, 0-100, not a fraction). NULL when the benchmark has
    -- no composition data; 0 when the benchmark has composition but no
    -- overlap with the industry's stocks.
    benchmark_shared_weight        NUMERIC(8,4),

    -- The following 3 columns are computed ONLY for broad-market benchmarks
    -- (stats.sec_index_tags.is_broad_market = TRUE). For non-broad benchmarks
    -- they remain NULL. They isolate the benchmark's movement EXCLUDING the
    -- stocks shared with this industry, so the shade between these values and
    -- the raw benchmark close shows the industry's contribution to the
    -- benchmark's performance.
    --
    -- CALCULATION (return-based decomposition):
    --   shared_stocks = intersection of benchmark composition and industry
    --                   member stocks union (latest sec_composition snapshot).
    --   shared_weight_fraction = benchmark_shared_weight / 100  (0..1)
    --   shared_portfolio_return_t = SUM(weight × stock_return_t) /
    --                               SUM(weight) for shared stocks on date t.
    --   non_industry_return_t = (benchmark_return_t -
    --                            shared_weight_fraction × shared_portfolio_return_t)
    --                           / (1 - shared_weight_fraction)
    --
    -- GUARDS (prevent numerical instability):
    --   * benchmark_shared_weight >= 95 → NULL (denominator too small).
    --   * non_industry_return capped at [-0.5, 0.5] (±50%); returns outside
    --     this range are treated as 0 in the rolling cumprod and NULL in
    --     the price computation.
    --
    --   price (today)  = benchmark_prev_close × (1 + non_industry_return_t).
    --                    Shows what today's close would be if ONLY non-shared
    --                    stocks moved today (shared stocks held flat).
    --   rolling_Xdays_price = 100 × cumprod(1 + non_industry_return) over the
    --                    trailing X-day window ending on `date` (X ∈
    --                    {5, 20, 60, 252, 504}). Shows the cumulative
    --                    performance of the non-shared portion over the last
    --                    X trading days. Returns outside [-0.5, 0.5] are
    --                    treated as 0 to prevent compounding artifacts.
    --   trading_amt    = benchmark.trading_amount - SUM(shared_stock.trading_amount).
    --                    The benchmark's turnover excluding the shared stocks.
    benchmark_non_this_industry_price      NUMERIC(20,4),
    benchmark_non_this_industry_rolling_5days_price        NUMERIC(20,4),
    benchmark_non_this_industry_rolling_20days_price       NUMERIC(20,4),
    benchmark_non_this_industry_rolling_120days_price      NUMERIC(20,4),
    benchmark_non_this_industry_rolling_60days_price       NUMERIC(20,4),
    benchmark_non_this_industry_rolling_252days_price      NUMERIC(20,4),
    benchmark_non_this_industry_rolling_504days_price      NUMERIC(20,4),
    benchmark_non_this_industry_trading_amt NUMERIC(20,4),

    -- Consolidated benchmark-offset primitive (added 2026-09-07) — read
    -- STRAIGHT from stats.cross_stats (sec_type='industry', which
    -- aggregates the pair-grain offsets over member indices): the
    -- industry's daily close change minus the benchmark's (SUM over
    -- member pairs), and its amount-weighted variant (offset × shared
    -- stocks' turnover share of benchmark turnover). Broad-market rows
    -- only; replaces the former local shared-weight recomputation for
    -- the offset view — the return-decomposition columns above keep
    -- their own semantics.
    code_price_with_benchmark_offset                 NUMERIC(20,6),
    code_price_with_benchmark_offset_by_weighted_amt NUMERIC(20,6),

    CONSTRAINT pk_industry_attributions PRIMARY KEY
        (industry_id, benchmark_code, date, attribution_type)
) PARTITION BY HASH (industry_id);

-- Native hash partitions (16) keyed by industry_id
-- Native hash partitions (16) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p15
SELECT public.create_hash_partitions('analysis', 'industry_attributions', 16);

-- Indexes:
--   1. PK (industry_id, benchmark_code, date, attribution_type) serves the
--      most common query pattern: WHERE industry_id = ... AND benchmark_code = ...
--      (HD pipeline, industry attribution). Also serves industry_id-only
--      queries via index scan.
--   2. Secondary (benchmark_code, date, industry_id) serves benchmark-first
--      queries: WHERE benchmark_code = ... AND date = ... (attribution bars,
--      benchmark price chart, HD pipeline source query).
CREATE INDEX IF NOT EXISTS idx_industry_attributions_bench_date_industry
    ON analysis.industry_attributions (benchmark_code, date, industry_id);

COMMENT ON TABLE  analysis.industry_attributions                  IS 'Composition overlap between each industry (group of member indices) and each benchmark index. One row per (date, industry_id, benchmark_code, attribution_type). TWO attribution variants: attribution_type=''trading_amt'' -> industry_shared_weight = SUM(code_sec_shared_weight) across member indices (can exceed 100); attribution_type=''equal'' -> industry_shared_weight = AVG = SUM / N (N = active member index count from stats.sec_classification). benchmark_shared_weight is UNDIVIDED (same for both variants — property of the benchmark composition, not member count), so ALL non_this_industry_* columns are IDENTICAL between variants. HYBRID aggregation: industry_shared_weight = SUM(code_sec_shared_weight) across member indices from analysis.sec_alloc_perf_attribution (each member contributes its OWN weight on shared stocks; can exceed 100; self-pairs excluded). benchmark_shared_weight = benchmark weight on the UNION of industry member stocks from stats.sec_composition (latest snapshot; bounded [0, 100] (percent); no double-counting). Recomputed from sec_composition (NOT summed from sec_alloc_perf_attribution) because a naive SUM of benchmark_sec_shared_weight across members would double-count stocks held by multiple members. Both columns use the LATEST sec_composition snapshot for all dates (temporal extrapolation). Built by analysis.market_beta.attributions (internal step, truncate-then-recompute). Depends on analysis.sec_alloc_perf_attribution being populated first.';
COMMENT ON COLUMN analysis.industry_attributions.industry_id           IS 'Subject industry_id (from stats.sec_classification type=''index''). The industry whose member indices'' overlap with the benchmark is aggregated.';
COMMENT ON COLUMN analysis.industry_attributions.benchmark_code        IS 'Benchmark index code (typically one of the 6 broad-market indices: 000300, 000001, 000852, 399001, 399006, 000688, plus per-industry top-N non-broad indices). Inherited from analysis.sec_alloc_perf_attribution.benchmark_code.';
COMMENT ON COLUMN analysis.industry_attributions.date                  IS 'Trading date. Inherited from analysis.sec_alloc_perf_attribution (dates where at least one member index has a row for the benchmark). Shared-weight values are constant per (industry_id, benchmark_code) across dates, but the sum can vary when member indices have unequal history lengths.';
COMMENT ON COLUMN analysis.industry_attributions.attribution_type       IS 'Attribution variant. ''trading_amt'': industry_shared_weight = SUM(code_sec_shared_weight) across member indices (can exceed 100). ''equal'': industry_shared_weight = AVG = SUM / N (N = active member index count from stats.sec_classification). benchmark_shared_weight is UNDIVIDED (same for both variants), so all non_this_industry_* columns are identical between variants.';
COMMENT ON COLUMN analysis.industry_attributions.industry_shared_weight  IS 'For attribution_type=''trading_amt'': SUM(code_sec_shared_weight) across member indices in the industry, from analysis.sec_alloc_perf_attribution (sec_type=''index''). Each member contributes its OWN weight on stocks shared with the benchmark. Can exceed 100 (sum of multiple member portfolios — expected, NOT double-counting). Self-pairs (member == benchmark) excluded by sec_alloc_perf_attribution. NULL when the benchmark has no composition data (all members'' code_sec_shared_weight are NULL). For attribution_type=''equal'': the same SUM divided by N (active member index count) = AVG(code_sec_shared_weight).';
COMMENT ON COLUMN analysis.industry_attributions.benchmark_shared_weight IS 'SUM(benchmark weight_pct) on the UNION of stocks held by ANY industry member (latest stats.sec_composition snapshot, source_type=''index''). Each stock counted ONCE (union) -> no double-counting. Bounded [0, 100] (weight_pct is stored as a percent, 0-100, not a fraction): fraction of the benchmark''s weight in the industry''s stock union. NULL when the benchmark has no composition data; 0 when the benchmark has composition but no overlap with the industry''s stocks. Recomputed from sec_composition (NOT summed from sec_alloc_perf_attribution) to avoid double-counting stocks held by multiple members.';
COMMENT ON COLUMN analysis.industry_attributions.benchmark_non_this_industry_price IS 'Benchmark close on the date with industry-shared stocks removed (today snapshot). Computed ONLY for broad-market benchmarks (is_broad_market=TRUE); NULL otherwise. Return-based decomposition: non_industry_return = (bench_return - swf × shared_portfolio_return) / (1 - swf), where swf = benchmark_shared_weight/100. price = bench_prev_close × (1 + non_industry_return). Shows what today''s close would be if only non-shared stocks moved today.';
COMMENT ON COLUMN analysis.industry_attributions.benchmark_non_this_industry_rolling_5days_price IS 'Non-industry benchmark price rebased to 100, computed over the trailing 5-trading-day window ending on `date`. Computed ONLY for broad-market benchmarks (is_broad_market=TRUE); NULL otherwise. = 100 × cumprod(1 + non_industry_return) over the last 5 trading days. Returns outside [-0.5, 0.5] are treated as 0 to prevent compounding artifacts. Shows the short-term cumulative performance of the benchmark''s non-shared portion. The BenchmarkPriceChart dropdown lets the user pick which window (5/20/60/252/504) to overlay.';
COMMENT ON COLUMN analysis.industry_attributions.benchmark_non_this_industry_rolling_20days_price IS 'Non-industry benchmark price rebased to 100, computed over the trailing 20-trading-day window ending on `date`. Computed ONLY for broad-market benchmarks (is_broad_market=TRUE); NULL otherwise. = 100 × cumprod(1 + non_industry_return) over the last 20 trading days. Returns outside [-0.5, 0.5] are treated as 0 to prevent compounding artifacts.';
COMMENT ON COLUMN analysis.industry_attributions.benchmark_non_this_industry_rolling_60days_price IS 'Non-industry benchmark price rebased to 100, computed over the trailing 60-trading-day window ending on `date`. Computed ONLY for broad-market benchmarks (is_broad_market=TRUE); NULL otherwise. = 100 × cumprod(1 + non_industry_return) over the last 60 trading days. Returns outside [-0.5, 0.5] are treated as 0 to prevent compounding artifacts.';
COMMENT ON COLUMN analysis.industry_attributions.benchmark_non_this_industry_rolling_252days_price IS 'Non-industry benchmark price rebased to 100, computed over the trailing 252-trading-day window ending on `date`. Computed ONLY for broad-market benchmarks (is_broad_market=TRUE); NULL otherwise. = 100 × cumprod(1 + non_industry_return) over the last 252 trading days (~1 year). Returns outside [-0.5, 0.5] are treated as 0 to prevent compounding artifacts.';
COMMENT ON COLUMN analysis.industry_attributions.benchmark_non_this_industry_rolling_504days_price IS 'Non-industry benchmark price rebased to 100, computed over the trailing 504-trading-day window ending on `date`. Computed ONLY for broad-market benchmarks (is_broad_market=TRUE); NULL otherwise. = 100 × cumprod(1 + non_industry_return) over the last 504 trading days (~2 years). Returns outside [-0.5, 0.5] are treated as 0 to prevent compounding artifacts.';
COMMENT ON COLUMN analysis.industry_attributions.benchmark_non_this_industry_trading_amt IS 'Benchmark trading_amount on the date minus SUM of shared stocks'' trading_amount on the date. Computed ONLY for broad-market benchmarks. Shows the benchmark''s turnover excluding the industry''s shared stocks. NULL when the benchmark has no trading_amount data or no shared stock data on that date.';

-- ----------------------------------------------------------------------------
--  Register in analysis.analysis_identity
-- ----------------------------------------------------------------------------
INSERT INTO analysis.analysis_identity (name, detail_name, summary_name, last_run_datetime, description) VALUES
    ('industry_attributions', 'industry_attributions', NULL, NOW(),
     'Composition overlap between each industry (group of member indices) and each benchmark index. One row per (date, industry_id, benchmark_code, attribution_type). TWO attribution variants: trading_amt -> industry_shared_weight = SUM(code_sec_shared_weight) across member indices (can exceed 100); equal -> industry_shared_weight = AVG = SUM / N (N = active member index count). benchmark_shared_weight is UNDIVIDED (same for both variants), so all non_this_industry_* columns are identical between variants. HYBRID aggregation: industry_shared_weight = SUM(code_sec_shared_weight) across member indices from analysis.sec_alloc_perf_attribution (own-weight on shared stocks in percent, can exceed 100, self-pairs excluded); benchmark_shared_weight = benchmark weight on the UNION of industry member stocks from stats.sec_composition (latest snapshot, in percent 0-100, no double-counting, recomputed from compositions to avoid double-counting stocks held by multiple members). Both use LATEST snapshot for all dates. Built by analysis.market_beta.attributions (internal step, truncate-then-recompute). Depends on analysis.sec_alloc_perf_attribution being populated first.')
ON CONFLICT (name) DO UPDATE SET
    detail_name       = EXCLUDED.detail_name,
    summary_name      = EXCLUDED.summary_name,
    last_run_datetime = NOW(),
    description       = EXCLUDED.description;

-- ----------------------------------------------------------------------------
--  Register in analysis.analysis_identity
-- ----------------------------------------------------------------------------
INSERT INTO analysis.analysis_identity (name, detail_name, summary_name, last_run_datetime, description) VALUES
    ('industry_correlations', 'industry_correlations', NULL, NOW(),
     'Windowed pairwise Pearson correlation between two industries'' MA curves of mean_close (stats.industry_basic_stats.mean_close, built by builds.industry — former mean_price, rehooked 2026-08-24). One row per (industry_id, benchmark_industry_id, pool_size, start_date, interval) with corr_ma20_20d / corr_ma60_60d / corr_ma252_252d. Windows start on the pool calendar grid every `interval` (default 20) trading days; corr_ma{W}_{W}d correlates the two industries'' MA-W curves over the W trading days starting on start_date. Both industries are compared in the SAME pool_size slice (single pool_size column). Self-pairs (A=B) excluded. Order convention: industry_id < benchmark_industry_id to deduplicate. Only same-pool slices materialized (all, small, mid, large). Built by analysis.market_beta.correlations (internal step, incremental / force).')
ON CONFLICT (name) DO UPDATE SET
    detail_name       = EXCLUDED.detail_name,
    summary_name      = EXCLUDED.summary_name,
    last_run_datetime = NOW(),
    description       = EXCLUDED.description;
COMMENT ON COLUMN analysis.industry_attributions.benchmark_non_this_industry_rolling_120days_price IS 'Non-industry benchmark price rebased to 100, computed over the trailing 120-trading-day window ending on `date`. Computed ONLY for broad-market benchmarks (is_broad_market=TRUE); NULL otherwise. = 100 × cumprod(1 + non_industry_return) over the last 120 trading days (~6 months). Returns outside [-0.5, 0.5] are treated as 0 to prevent compounding artifacts. Default period for the BenchmarkPriceChart shade overlay and for analysis.industry_hypes_and_drains.';
