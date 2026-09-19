-- ============================================================================
--  Industry Hypes & Drains — pre-computed top-5 / bottom-5 industries ranked
--  by their attribution contribution to a BROAD-MARKET benchmark over a
--  trailing window.
--
--  Table: analysis.industry_hypes_and_drains
--    PK: (date, benchmark_code, period_days, rank_side, rank)
--
--  PURPOSE
--    "Benchmark Attribution" mode on the Industry Sentiments page lets the
--    user pick ONE industry and see its contribution to each benchmark.
--    This table inverts that: for every (date, benchmark, window) it
--    pre-computes the 5 industries that most ELEVATED the benchmark
--    (HYPE — positive contribution) and the 5 that most DRAINED it (DRAIN —
--    negative contribution), so the Market Trend page can plot the
--    "significantly higher and lower industry curves" against the benchmark
--    with a frozen (non-user-selectable) classification nav.
--
--  BENCHMARKS
--    Uses the SAME broad-market benchmarks as the Benchmark Attribution
--    view (the ★ benchmarks from listIndustryAttributionBenchmarks — i.e.
--    all benchmark_codes in analysis.industry_attributions that have
--    is_broad_market=TRUE in stats.sec_index_tags). The UI offers the same
--    Autocomplete dropdown as Benchmark Attribution.
--
--  METRIC (hype = industry_return - benchmark_return)
--    The industry return is the SHARED PORTFOLIO's return (the benchmark
--    members that belong to the industry), recovered by inverting the
--    DAILY return-decomposition identity — never by inverting the
--    materialized rolling_{N}days_price column (its clamped / NULL->0 /
--    compounding artifacts the (1-swf)/swf inversion would amplify into
--    +-hundreds-of-percent noise for narrow industries).
--    For each (date, industry, benchmark, period N):
--      bench_return_1d        = close[t] / close[t-1] - 1
--      non_industry_return_1d = benchmark_non_this_industry_price[t] /
--                               close[t-1] - 1   (EXACT: the build
--                               materializes the level as
--                               price[t] = close[t-1] * (1 + r_t))
--      swf                    = benchmark_shared_weight / 100.0
--      shared_return_1d       = (bench_return_1d - (1-swf)*non_industry_return_1d) / swf
--      industry_return_Nd     = compounded shared_return_1d over the FULL
--                               trailing N trading-day rows (exp of the
--                               summed ln(1+r); returns outside (-0.5, 0.5]
--                               contribute 0; partial windows -> NULL)
--      benchmark_return_Nd    = benchmark.close[t] / benchmark.close[t-N] - 1
--      hype                   = industry_return_Nd - benchmark_return_Nd
--
--    A POSITIVE hype means the industry's shared stocks OUTPERFORMED the
--    benchmark (HYPE); NEGATIVE means they UNDERPERFORMED (DRAIN).
--    Industries are ranked by hype DESC; rank 1..5 HYPE = top 5,
--    rank 1..5 DRAIN = bottom 5. Industries with NULL hype (no overlap
--    with the benchmark, swf = 0, or insufficient history for the full
--    N-row window) are excluded from ranking.
--
--    For weighting='amt': metric_value = hype * shared_trading_amt (absolute
--    yuan impact). The ranking is by this amount instead of raw hype.
--
--  PERIODS
--    period_days ∈ {5, 20, 60, 120, 255, 500} trading days. 120d is the
--    UI default (see ROLLING_DAYS in the frontend constants). The 120d
--    column on analysis.industry_attributions is populated by the
--    attributions step (which includes 120 in ROLLING_WINDOWS).
--
--  SOURCE
--    analysis.industry_attributions  (benchmark_non_this_industry_rolling_{N}days_price
--                                     + benchmark_shared_weight per
--                                     (date, industry_id, benchmark_code))
--    stats.index_basic_stats         (benchmark closes + trading_amount)
--    stats.sec_classification        (industry_label per industry_id)
--    stats.sec_index_tags            (is_broad_market flag)
--
--  POPULATION
--    analyze.industry_sentiments.hypes_and_drains (internal step
--    run_hypes_and_drains, invoked from __main__ after attributions).
--    Truncate-then-recompute on every run. Depends on
--    analysis.industry_attributions being populated first (and on the 120d
--    column having been backfilled).
--
--  Register in analysis.analysis_identity (name='industry_hypes_and_drains').
-- ============================================================================

COMMENT ON COLUMN analysis.industry_attributions.benchmark_non_this_industry_rolling_120days_price IS 'Non-industry benchmark price rebased to 100, computed over the trailing 120-trading-day window ending on `date`. Computed ONLY for broad-market benchmarks (is_broad_market=TRUE); NULL otherwise. = 100 × cumprod(1 + non_industry_return) over the last 120 trading days (~6 months). Returns outside [-0.5, 0.5] are treated as 0 to prevent compounding artifacts. Default period for the BenchmarkPriceChart shade overlay and for analysis.industry_hypes_and_drains.';


-- ----------------------------------------------------------------------------
--  Table: analysis.industry_hypes_and_drains
-- ----------------------------------------------------------------------------
DROP TABLE IF EXISTS analysis.industry_hypes_and_drains;

CREATE TABLE IF NOT EXISTS analysis.industry_hypes_and_drains (
    date                          DATE          NOT NULL,
    benchmark_code                TEXT          NOT NULL,  -- broad-market index code (e.g. 000300, 000001, ...)
    period_days                   INTEGER       NOT NULL,  -- 5 | 20 | 60 | 120 | 255 | 500
    weighting                     TEXT          NOT NULL DEFAULT 'equal',  -- 'equal' | 'amt'
    rank_side                     TEXT          NOT NULL,  -- 'HYPE' | 'DRAIN'
    rank                          SMALLINT      NOT NULL,  -- 1 | 2 | 3 | 4 | 5

    industry_id                   TEXT          NOT NULL,
    industry_label                TEXT          NOT NULL DEFAULT '',

    -- Ranking metric. For weighting='equal': hype = industry_return_Nd -
    -- benchmark_return_Nd (range ~[-1, 1]) where industry_return_Nd is
    -- derived from the return decomposition.
    -- For weighting='amt': hype × shared_trading_amt (absolute yuan,
    -- can be ~10^8-10^11). Positive = HYPE, negative = DRAIN in both cases.
    metric_value                  NUMERIC(24,6),

    -- Shared trading amount (yuan) = benchmark.trading_amount
    -- - benchmark_non_this_industry_trading_amt. The industry's shared
    -- stocks' turnover on this date. NULL when trading amount data is
    -- unavailable (→ that industry is excluded from amt-weighted ranking).
    shared_trading_amt            NUMERIC(24,4),

    -- Benchmark N-day return (signed). Stored for the UI tooltip
    -- so the user can see both the benchmark move and the contribution.
    benchmark_return_nd           NUMERIC(10,6),

    -- Industry (shared portfolio) N-day return (signed) = the compounded
    -- daily shared return over the trailing N trading-day rows (see the
    -- METRIC block above). hype = industry_return_Nd - benchmark_return_Nd.
    industry_return_nd            NUMERIC(10,6),

    -- Industry's benchmark_shared_weight (latest snapshot, percent 0-100).
    -- Tooltip context: how much of the benchmark the industry's stocks
    -- represent.
    benchmark_shared_weight       NUMERIC(8,4),

    CONSTRAINT pk_industry_hypes_and_drains PRIMARY KEY
        (benchmark_code, date, period_days, weighting, rank_side, rank),
    CONSTRAINT chk_hypes_period_days    CHECK (period_days IN (5, 20, 60, 120, 255, 500)),
    CONSTRAINT chk_hypes_weighting      CHECK (weighting IN ('equal', 'amt')),
    CONSTRAINT chk_hypes_rank_side      CHECK (rank_side IN ('HYPE', 'DRAIN')),
    CONSTRAINT chk_hypes_rank           CHECK (rank BETWEEN 1 AND 5)
) PARTITION BY HASH (benchmark_code);

-- Native hash partitions (16) keyed by benchmark_code
-- Native hash partitions (16) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p15
SELECT public.create_hash_partitions('analysis', 'industry_hypes_and_drains', 16);

-- Indexes:
--   1. Per-(benchmark_code, period, weighting, date) lookup — the UI fetches
--      the 10 ranked industries for one (benchmark, period, weighting, date).
--   2. Per-industry time series (drives any future "industry ranking over
--      time" view).
CREATE INDEX IF NOT EXISTS idx_hypes_bench_period_date
    ON analysis.industry_hypes_and_drains (benchmark_code, period_days, weighting, date);
CREATE INDEX IF NOT EXISTS idx_hypes_industry_bench_period_date
    ON analysis.industry_hypes_and_drains (industry_id, benchmark_code, period_days, weighting, date);

COMMENT ON TABLE  analysis.industry_hypes_and_drains                IS 'Pre-computed top-5 (HYPE) + bottom-5 (DRAIN) industries ranked by hype (industry_return - benchmark_return) relative to a BROAD-MARKET benchmark over a trailing window. One row per (benchmark_code, date, period_days, weighting, rank_side, rank). weighting: equal (metric_value = hype, attribution_type=equal) or amt (metric_value = hype × shared_trading_amt, attribution_type=trading_amt). hype = industry_return_Nd - benchmark_return_Nd where industry_return_Nd is the shared portfolio''s trailing-N-day return, recovered by inverting the DAILY decomposition identity (shared_return_1d = (bench_1d - (1-swf)*non_ind_1d)/swf, swf = benchmark_shared_weight/100) and compounding over the full N-row window — never by inverting the materialized rolling column, whose construction artifacts the 1/swf inversion would amplify into noise. benchmark_code: any broad-market index (is_broad_market=TRUE in stats.sec_index_tags). Positive=HYPE, negative=DRAIN. Built by analyze.industry_sentiments.hypes_and_drains (internal step, truncate-then-recompute). Depends on analysis.industry_attributions (incl. the daily benchmark_non_this_industry_price column) being populated first.';
COMMENT ON COLUMN analysis.industry_hypes_and_drains.weighting      IS 'Ranking method: equal = hype (industry_return_Nd - benchmark_return_Nd, attribution_type=equal). amt = hype × shared_trading_amt (absolute yuan impact, attribution_type=trading_amt). The UI toggle switches between these two ranking methods.';
COMMENT ON COLUMN analysis.industry_hypes_and_drains.metric_value  IS 'Ranking metric. For weighting=equal: hype = industry_return_Nd - benchmark_return_Nd where industry_return_Nd is the shared portfolio''s compounded trailing-N-day return (daily-identity inversion). For weighting=amt: hype × shared_trading_amt (absolute yuan, can be ~10^8-10^11). Positive = HYPE, negative = DRAIN in both cases.';
COMMENT ON COLUMN analysis.industry_hypes_and_drains.shared_trading_amt IS 'Shared stocks trading amount (yuan) = benchmark.trading_amount - benchmark_non_this_industry_trading_amt. NULL when trading amount data is unavailable. Used to compute the amt-weighted metric and for UI tooltip context.';
COMMENT ON COLUMN analysis.industry_hypes_and_drains.benchmark_return_nd    IS 'Benchmark N-day return (signed) = close[t]/close[t-N]-1. Stored for the UI tooltip.';
COMMENT ON COLUMN analysis.industry_hypes_and_drains.industry_return_nd IS 'Industry (shared portfolio) N-day return (signed) = compounded daily shared return over the trailing N trading-day rows; the daily shared return is inverted from the decomposition identity: shared_return_1d = (bench_return_1d - (1-swf)*non_industry_return_1d) / swf with swf = benchmark_shared_weight/100. hype = industry_return_Nd - benchmark_return_Nd. NULL when the industry has no overlap with the benchmark (swf = 0) or the window is not full.';
COMMENT ON COLUMN analysis.industry_hypes_and_drains.benchmark_shared_weight IS 'Industry''s benchmark_shared_weight (latest sec_composition snapshot, in percent 0-100). Tooltip context: how much of the benchmark the industry''s stocks represent. NULL when the industry has no overlap with the benchmark.';

-- ----------------------------------------------------------------------------
--  Register in analysis.analysis_identity
-- ----------------------------------------------------------------------------
INSERT INTO analysis.analysis_identity (name, detail_name, summary_name, last_run_datetime, description) VALUES
    ('industry_hypes_and_drains', 'industry_hypes_and_drains', NULL, NOW(),
     'Pre-computed top-5 (HYPE) + bottom-5 (DRAIN) industries ranked by hype (industry_return - benchmark_return) relative to a BROAD-MARKET benchmark over a trailing window. One row per (date, benchmark_code, period_days, weighting, rank_side, rank). Two weighting variants: equal (metric_value=hype, attribution_type=equal) and amt (metric_value=hype*shared_trading_amt, attribution_type=trading_amt). hype = industry_return_Nd - benchmark_return_Nd where industry_return_Nd is the shared portfolio''s trailing-N-day return, recovered by inverting the DAILY decomposition identity (shared_return_1d = (bench_1d - (1-swf)*non_ind_1d)/swf) and compounding over the full N-row window. period_days in {5,20,60,120,255,500} (120 default). Built by analyze.industry_sentiments.hypes_and_drains (internal step, truncate-then-recompute). Depends on analysis.industry_attributions (incl. the daily benchmark_non_this_industry_price column) being populated first.')
ON CONFLICT (name) DO UPDATE SET
    detail_name       = EXCLUDED.detail_name,
    summary_name      = EXCLUDED.summary_name,
    last_run_datetime = NOW(),
    description       = EXCLUDED.description;
