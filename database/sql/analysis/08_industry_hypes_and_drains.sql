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
--    For each (date, industry, benchmark, period N):
--      non_industry_return_Nd = benchmark.benchmark_non_this_industry_rolling_{N}days_price
--                               / 100 - 1   (cumulative non-industry return
--                               factor over the trailing N trading days;
--                               NULL when the industry has no overlap with
--                               the benchmark or the benchmark's shared
--                               weight >= 95%)
--      benchmark_return_Nd    = benchmark.close[t] / benchmark.close[t-N] - 1
--      swf                    = benchmark_shared_weight / 100.0
--      industry_return_Nd     = (benchmark_return_Nd - (1 - swf) * non_industry_return_Nd) / swf
--      hype                   = industry_return_Nd - benchmark_return_Nd
--
--    A POSITIVE hype means the industry's shared stocks OUTPERFORMED the
--    benchmark (HYPE); NEGATIVE means they UNDERPERFORMED (DRAIN).
--    Industries are ranked by hype DESC; rank 1..5 HYPE = top 5,
--    rank 1..5 DRAIN = bottom 5. Industries with NULL hype (no overlap
--    with the benchmark, swf = 0, or insufficient history) are excluded
--    from ranking.
--
--    For weighting='amt': metric_value = hype * shared_trading_amt (absolute
--    yuan impact). The ranking is by this amount instead of raw hype.
--
--  PERIODS
--    period_days ∈ {5, 20, 60, 120, 255, 500} trading days. 120d is the
--    UI default (see ROLLING_DAYS in the frontend constants). The 120d
--    column on analysis.industry_attributions is added below via ALTER
--    TABLE and populated by the attributions step (which now includes 120
--    in ROLLING_WINDOWS).
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

-- ----------------------------------------------------------------------------
--  Add the 120-day rolling column to analysis.industry_attributions.
--  Idempotent: ADD COLUMN IF NOT EXISTS. Populated by the attributions step
--  (ROLLING_WINDOWS now includes 120). Existing rows get NULL until a
--  backfill / force run; the hypes_and_drains step skips period=120 rows
--  whose contribution source column is NULL.
-- ----------------------------------------------------------------------------
ALTER TABLE analysis.industry_attributions
    ADD COLUMN IF NOT EXISTS benchmark_non_this_industry_rolling_120days_price NUMERIC(20,4);

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

    -- Non-this-industry N-day return (signed) = the benchmark
    -- move EXCLUDING the industry's shared stocks. Used to derive
    -- industry_return_Nd (and thus hype) via the return decomposition:
    --   swf = benchmark_shared_weight / 100
    --   industry_return_Nd = (benchmark_return_Nd - (1-swf)*non_industry_return_Nd) / swf
    --   hype = industry_return_Nd - benchmark_return_Nd
    non_industry_return_nd        NUMERIC(10,6),

    -- Industry's benchmark_shared_weight (latest snapshot, percent 0-100).
    -- Tooltip context: how much of the benchmark the industry's stocks
    -- represent.
    benchmark_shared_weight       NUMERIC(8,4),

    CONSTRAINT pk_industry_hypes_and_drains PRIMARY KEY
        (date, benchmark_code, period_days, weighting, rank_side, rank),
    CONSTRAINT chk_hypes_period_days    CHECK (period_days IN (5, 20, 60, 120, 255, 500)),
    CONSTRAINT chk_hypes_weighting      CHECK (weighting IN ('equal', 'amt')),
    CONSTRAINT chk_hypes_rank_side      CHECK (rank_side IN ('HYPE', 'DRAIN')),
    CONSTRAINT chk_hypes_rank           CHECK (rank BETWEEN 1 AND 5)
);

-- Indexes:
--   1. Per-(benchmark_code, period, weighting, date) lookup — the UI fetches
--      the 10 ranked industries for one (benchmark, period, weighting, date).
--   2. Per-industry time series (drives any future "industry ranking over
--      time" view).
CREATE INDEX IF NOT EXISTS idx_hypes_bench_period_date
    ON analysis.industry_hypes_and_drains (benchmark_code, period_days, weighting, date);
CREATE INDEX IF NOT EXISTS idx_hypes_industry_bench_period_date
    ON analysis.industry_hypes_and_drains (industry_id, benchmark_code, period_days, weighting, date);

COMMENT ON TABLE  analysis.industry_hypes_and_drains                IS 'Pre-computed top-5 (HYPE) + bottom-5 (DRAIN) industries ranked by hype (industry_return - benchmark_return) relative to a BROAD-MARKET benchmark over a trailing window. One row per (date, benchmark_code, period_days, weighting, rank_side, rank). weighting: equal (metric_value = hype, attribution_type=equal) or amt (metric_value = hype × shared_trading_amt, attribution_type=trading_amt). hype = industry_return_Nd - benchmark_return_Nd where industry_return_Nd = (bench_ret - (1-swf)*non_ind_ret) / swf and swf = benchmark_shared_weight / 100. benchmark_code: any broad-market index (is_broad_market=TRUE in stats.sec_index_tags). Positive=HYPE, negative=DRAIN. Built by analyze.industry_sentiments.hypes_and_drains (internal step, truncate-then-recompute). Depends on analysis.industry_attributions (incl. the 120d column) being populated first.';
COMMENT ON COLUMN analysis.industry_hypes_and_drains.weighting      IS 'Ranking method: equal = hype (industry_return_Nd - benchmark_return_Nd, attribution_type=equal). amt = hype × shared_trading_amt (absolute yuan impact, attribution_type=trading_amt). The UI toggle switches between these two ranking methods.';
COMMENT ON COLUMN analysis.industry_hypes_and_drains.metric_value  IS 'Ranking metric. For weighting=equal: hype = industry_return_Nd - benchmark_return_Nd (range ~[-1,1]) where industry_return_Nd = (bench_ret - (1-swf)*non_ind_ret)/swf and swf = benchmark_shared_weight/100. For weighting=amt: hype × shared_trading_amt (absolute yuan, can be ~10^8-10^11). Positive = HYPE, negative = DRAIN in both cases.';
COMMENT ON COLUMN analysis.industry_hypes_and_drains.shared_trading_amt IS 'Shared stocks trading amount (yuan) = benchmark.trading_amount - benchmark_non_this_industry_trading_amt. NULL when trading amount data is unavailable. Used to compute the amt-weighted metric and for UI tooltip context.';
COMMENT ON COLUMN analysis.industry_hypes_and_drains.benchmark_return_nd    IS 'Benchmark N-day return (signed) = close[t]/close[t-N]-1. Stored for the UI tooltip.';
COMMENT ON COLUMN analysis.industry_hypes_and_drains.non_industry_return_nd IS 'Non-this-industry N-day return (signed) = the benchmark move EXCLUDING the industry''s shared stocks = non_this_industry_rolling_{N}days_price / 100 - 1. Used to derive industry_return_Nd (and thus hype) via the return decomposition: industry_return_Nd = (benchmark_return_Nd - (1-swf)*non_industry_return_Nd) / swf.';
COMMENT ON COLUMN analysis.industry_hypes_and_drains.benchmark_shared_weight IS 'Industry''s benchmark_shared_weight (latest sec_composition snapshot, in percent 0-100). Tooltip context: how much of the benchmark the industry''s stocks represent. NULL when the industry has no overlap with the benchmark.';

-- ----------------------------------------------------------------------------
--  Register in analysis.analysis_identity
-- ----------------------------------------------------------------------------
INSERT INTO analysis.analysis_identity (name, detail_name, summary_name, last_run_datetime, description) VALUES
    ('industry_hypes_and_drains', 'industry_hypes_and_drains', NULL, NOW(),
     'Pre-computed top-5 (HYPE) + bottom-5 (DRAIN) industries ranked by hype (industry_return - benchmark_return) relative to a BROAD-MARKET benchmark over a trailing window. One row per (date, benchmark_code, period_days, weighting, rank_side, rank). Two weighting variants: equal (metric_value=hype, attribution_type=equal) and amt (metric_value=hype*shared_trading_amt, attribution_type=trading_amt). hype = industry_return_Nd - benchmark_return_Nd where industry_return_Nd = (bench_ret - (1-swf)*non_ind_ret)/swf and swf = benchmark_shared_weight/100. period_days in {5,20,60,120,255,500} (120 default). Built by analyze.industry_sentiments.hypes_and_drains (internal step, truncate-then-recompute). Depends on analysis.industry_attributions (incl. 120d column) being populated first.')
ON CONFLICT (name) DO UPDATE SET
    detail_name       = EXCLUDED.detail_name,
    summary_name      = EXCLUDED.summary_name,
    last_run_datetime = NOW(),
    description       = EXCLUDED.description;
