-- ============================================================================
--  Industry Sentiments — cross-sectional aggregation of REBASED-TO-100 index
--  levels across member indices within each industry, bucketed by pool_size.
--
--  Table: analysis.industry_sentiments
--    PK: (date, industry_id, pool_size)
--    pool_size ∈ ('small','mid','large','all')
--      small = stock_num < 51    (tight thematic indices, e.g. 中证银行 50)
--      mid   = stock_num 51-180  (mid-cap baskets, e.g. CSI 100/200)
--      large = stock_num > 180   (broad baskets, e.g. CSI 300/500/800/1000)
--      all   = every member index regardless of pool size
--    One row per (date, industry_id, pool_size) slice stores the MEAN and
--    VARIANCE of the rebased-to-100 values across member indices in that slice
--    on that date.
--
--  REBASE CONVENTION (fixed at history start, scale-invariant)
--    Each member index's close series is rebased to 100 at its FIRST available
--    close (history start, per-index first date — indices listed later start
--    at 100 on their own first date). This makes member indices comparable
--    regardless of absolute price level — e.g. CSI 500 (~5500pts) and SSE 50
--    (~2600pts) both start at 100, so a +10% move on either looks equally
--    large. Mean and var are computed across these rebased-to-100 values.
--
--    NOTE: the rebased-to-100 ANCHOR is the START OF ALL HISTORY (fixed
--    server-side). The frontend multi-line plot uses a CLIENT-SIDE slider
--    that re-rebases the LINES to the slider's window-start — so the mean/var
--    overlay and the lines are aligned only when the slider is at full range.
--    When the slider narrows, the lines re-rebase but the mean/var overlay
--    stays anchored at history start. This tradeoff was chosen by the user:
--    server-side precompute is cleanest with a single fixed anchor.
--
--  BROAD-MARKET INDICES
--    Broad-market benchmarks (CSI 300, SSE 50, etc.) are classified in
--    stats.sec_classification under industry_ids BROAD_CSI, BROAD_SSE,
--    BROAD_SZSE, BROAD_STAR — they appear as 'industries' in the themes tree
--    and are aggregated IDENTICALLY to industry indices (no special handling).
--    The 'all' pool_size slice for BROAD_* industries gives the broad-market
--    aggregate sentiment.
--
--  SOURCE
--    stats.index_basic_stats.close    (raw daily index closes)
--    JOIN stats.sec_classification    (type='index') for industry membership
--    stats.sec_composition            (stock_num → pool_size classification)
--
--  COMPOSITION-ONLY FILTER
--    Only indices that have at least one snapshot in stats.sec_composition
--    (source_type='index') are included. Indices WITHOUT any composition
--    data are dropped entirely — they contribute nothing to any pool_size
--    slice. pool_size classification is only meaningful for indices whose
--    member count is known, and the 'all' slice reflects compositioned
--    indices only.
--
--  POPULATION
--    analyze_industry_sentiments.py (truncate-then-recompute on every run).
--    Rebase point is per-index first-available close (history start). Per
--    (date, industry_id, pool_size), aggregates rebased-to-100 values across
--    member indices in that slice: mean and var. index_count = number of
--    member indices contributing on that date.
--
--  Register in analysis.analysis_identity (name='industry_sentiments').
-- ============================================================================
DROP TABLE IF EXISTS analysis.industry_sentiments;

CREATE TABLE IF NOT EXISTS analysis.industry_sentiments (
    date                      DATE          NOT NULL,
    industry_id               TEXT          NOT NULL,
    pool_size                 TEXT          NOT NULL,  -- 'small' | 'mid' | 'large' | 'all'

    -- Display label (denormalized)
    industry_label            TEXT          NOT NULL DEFAULT '',

    -- Number of member indices contributing to this (date, industry_id, pool_size) slice.
    index_count               INTEGER,

    -- Cross-sectional MEAN of rebased-to-100 values across member indices in
    -- this this date.  this date. 100 = members flat vs history start.
    mean_rebased              NUMERIC(12,6),


    -- Cross-sectional VARIANCE of rebased-to-100 values across member indices
    -- in this this date.  this date. Captures cross-index dispersion
    -- (how spread out the member indices are on this date).
    var_rebased               NUMERIC(20,6),

    CONSTRAINT pk_industry_sentiments PRIMARY KEY (date, industry_id, pool_size),
    CONSTRAINT chk_industry_sentiments_pool
        CHECK (pool_size IN ('small', 'mid', 'large', 'all'))
);

-- Indexes for the common access patterns:
--   1. Per-industry + pool_size time series (drives the chart on the
--      IndustrySentiments page).
--   2. Per-date snapshot (drives the latest-date industries list).
CREATE INDEX IF NOT EXISTS idx_industry_sentiments_industry_pool_date
    ON analysis.industry_sentiments (industry_id, pool_size, date);
CREATE INDEX IF NOT EXISTS idx_industry_sentiments_date_industry
    ON analysis.industry_sentiments (date, industry_id);

COMMENT ON TABLE  analysis.industry_sentiments                IS 'Industry sentiment cross-section (rebased-to-100 levels): one row per (date, industry_id, pool_size). Aggregates rebased-to-100 index values across member indices (stats.sec_classification type=''index'' AND industry_id matches AND index has composition data in stats.sec_composition source_type=''index'') in the named pool_size slice. Indices WITHOUT composition data are excluded entirely. Rebased-to-100 at each index''s first available close (history start). pool_size: small (stock_num<51), mid (51-180), large (>180), all (every compositioned member). Stats: mean_rebased + var_rebased. Broad-market industries BROAD_CSI/BROAD_SSE/BROAD_SZSE/BROAD_STAR are aggregated identically. Built by analyze_industry_sentiments.py (truncate-then-recompute).';
COMMENT ON COLUMN analysis.industry_sentiments.pool_size      IS 'Pool-size slice: small (stock_num<51), mid (51-180), large (>180), all (every member index). Classification source: stats.sec_composition (latest snapshot <= date). NULL stock_num (compositioned index before its first snapshot) contributes to ''all'' only.';
COMMENT ON COLUMN analysis.industry_sentiments.index_count    IS 'Number of distinct member indices contributing to this (date, industry_id, pool_size) slice on this date.';
COMMENT ON COLUMN analysis.industry_sentiments.mean_rebased   IS 'AVG(rebased_to_100) across member indices in this pool_size slice on this date. Rebased-to-100 at each index''s first available close (history start). 100 = members flat vs their history-start value.';
COMMENT ON COLUMN analysis.industry_sentiments.var_rebased    IS 'VARIANCE(rebased_to_100) across member indices in this pool_size slice on this date. Captures cross-index dispersion (how spread out the members are).';

-- ============================================================================
--  Industry Correlations — pairwise rolling Pearson correlation of the
--  MEAN rebased-to-100 series between two industries, bucketed by pool_size.
--
--  Table: analysis.industry_correlations
--    PK: (date, industry_id, benchmark_industry_id,
--         industry_pool_size, benchmark_industry_pool_size)
--
--  SOURCE
--    analysis.industry_sentiments.mean_rebased   (per-industry mean series)
--
--  CONVENTION
--    For each pair of industries (A, B) and each SAME pool_size slice P
--    (i.e. industry_pool_size = benchmark_industry_pool_size = P), this
--    table stores the rolling-window Pearson correlation between A's
--    mean_rebased series and B's mean_rebased series on each date where the
--    two industries share enough overlapping history.
--
--    Cross-pool comparisons (e.g. corr(A.small_mean, B.large_mean)) are NOT
--    materialized — they conflate cross-index size effects with sentiment
--    co-movement and are not meaningful for industry-vs-industry analysis.
--    Only the 4 same-pool slices (all/all, small/small, mid/mid, large/large)
--    are populated.
--
--    Self-pairs (A = B) are NOT materialized — self-correlation is always 1.
--
--    Order convention: rows are stored with industry_id < benchmark_industry_id
--    (lexicographic) to deduplicate (A,B) vs (B,A). The API returns rows
--    matching either direction of the user-selected industry_ids set.
--
--  WINDOWS
--    5d / 20d / 60d / 255d rolling windows ending on `date`. NULL when fewer
--    than `window` overlapping (date, mean_rebased) pairs are available.
--
--  POPULATION
--    analyze_industry_correlations.py (truncate-then-recompute on every run).
-- ============================================================================
DROP TABLE IF EXISTS analysis.industry_correlations;

CREATE TABLE IF NOT EXISTS analysis.industry_correlations (
    industry_id                       TEXT          NOT NULL,
    benchmark_industry_id             TEXT          NOT NULL,
    industry_pool_size                TEXT          NOT NULL,
    benchmark_industry_pool_size      TEXT          NOT NULL,
    date                              DATE          NOT NULL,

    -- Rolling Pearson correlation between the two industries' mean_rebased
    -- series over the named window ending on `date`. NULL when insufficient
    -- overlap (< window days) on or before `date`.
    industry_mean_corr_5d             NUMERIC(8,4),
    industry_mean_corr_20d            NUMERIC(8,4),
    industry_mean_corr_60d            NUMERIC(8,4),
    industry_mean_corr_255d           NUMERIC(8,4),

    CONSTRAINT pk_industry_correlations PRIMARY KEY
        (date, industry_id, benchmark_industry_id,
         industry_pool_size, benchmark_industry_pool_size),
    CONSTRAINT chk_industry_correlations_industry_pool
        CHECK (industry_pool_size IN ('small', 'mid', 'large', 'all')),
    CONSTRAINT chk_industry_correlations_benchmark_pool
        CHECK (benchmark_industry_pool_size IN ('small', 'mid', 'large', 'all')),
    CONSTRAINT chk_industry_correlations_same_pool
        CHECK (industry_pool_size = benchmark_industry_pool_size),
    -- NOTE: COLLATE "C" forces byte-wise comparison so the lexicographic
    -- ordering invariant matches Python's default str sort (Python compares
    -- strings by Unicode code point). The database default collation is
    -- en_US.UTF-8, where punctuation like '_' sorts BEFORE letters — that
    -- would mismatch Python's sort and cause CHECK violations on pairs
    -- like (CONSUMER_ELEC, CONS_GENERAL).
    CONSTRAINT chk_industry_correlations_order
        CHECK (industry_id COLLATE "C" < benchmark_industry_id COLLATE "C")
);

-- Indexes:
--   1. Per-pair time series (drives the Correlation chart on the
--      IndustrySentiments page — fetch by industry_ids + pool_size).
--   2. Per-date snapshot (drives the latest-date correlation matrix).
CREATE INDEX IF NOT EXISTS idx_industry_correlations_pair_pool_date
    ON analysis.industry_correlations
    (industry_id, benchmark_industry_id, industry_pool_size, date);
CREATE INDEX IF NOT EXISTS idx_industry_correlations_bench_pair_pool_date
    ON analysis.industry_correlations
    (benchmark_industry_id, industry_id, benchmark_industry_pool_size, date);
CREATE INDEX IF NOT EXISTS idx_industry_correlations_date
    ON analysis.industry_correlations (date);

COMMENT ON TABLE  analysis.industry_correlations                            IS 'Pairwise rolling Pearson correlation between two industries'' mean_rebased series (analysis.industry_sentiments.mean_rebased). One row per (date, industry_id, benchmark_industry_id, pool_size, pool_size). Self-pairs (A=B) excluded. Order convention: industry_id < benchmark_industry_id (lexicographic) to deduplicate (A,B) vs (B,A). Only same-pool slices materialized (all/all, small/small, mid/mid, large/large). Built by analyze_industry_correlations.py (truncate-then-recompute).';
COMMENT ON COLUMN analysis.industry_correlations.industry_id                IS 'Subject industry''s industry_id (lexicographically smaller of the two).';
COMMENT ON COLUMN analysis.industry_correlations.benchmark_industry_id       IS 'Benchmark industry''s industry_id (lexicographically larger of the two).';
COMMENT ON COLUMN analysis.industry_correlations.industry_pool_size         IS 'Subject industry''s pool_size slice (same as benchmark_industry_pool_size — only same-pool slices are materialized). small (stock_num<51), mid (51-180), large (>180), all (every member).';
COMMENT ON COLUMN analysis.industry_correlations.benchmark_industry_pool_size IS 'Benchmark industry''s pool_size slice (same as industry_pool_size).';
COMMENT ON COLUMN analysis.industry_correlations.date                       IS 'End date of the rolling correlation window.';
COMMENT ON COLUMN analysis.industry_correlations.industry_mean_corr_5d      IS 'Pearson correlation between the two industries'' mean_rebased series over the trailing 5 trading days ending on `date`. NULL when < 5 overlapping days on or before `date`.';
COMMENT ON COLUMN analysis.industry_correlations.industry_mean_corr_20d     IS 'Pearson correlation between the two industries'' mean_rebased series over the trailing 20 trading days ending on `date`. NULL when < 20 overlapping days on or before `date`.';
COMMENT ON COLUMN analysis.industry_correlations.industry_mean_corr_60d     IS 'Pearson correlation between the two industries'' mean_rebased series over the trailing 60 trading days ending on `date`. NULL when < 60 overlapping days on or before `date`.';
COMMENT ON COLUMN analysis.industry_correlations.industry_mean_corr_255d    IS 'Pearson correlation between the two industries'' mean_rebased series over the trailing 255 trading days ending on `date`. NULL when < 255 overlapping days on or before `date`.';

-- ----------------------------------------------------------------------------
--  Register in analysis.analysis_identity
-- ----------------------------------------------------------------------------
INSERT INTO analysis.analysis_identity (name, detail_name, summary_name, last_run_datetime, description) VALUES
    ('industry_correlations', 'industry_correlations', NULL, NOW(),
     'Pairwise rolling Pearson correlation between two industries'' mean_rebased series (analysis.industry_sentiments.mean_rebased). One row per (date, industry_id, benchmark_industry_id, pool_size, pool_size) with corr_5d / corr_20d / corr_60d / corr_255d. Self-pairs (A=B) excluded. Order convention: industry_id < benchmark_industry_id to deduplicate. Only same-pool slices materialized (all/all, small/small, mid/mid, large/large). Built by analyze_industry_correlations.py (truncate-then-recompute).')
ON CONFLICT (name) DO UPDATE SET
    detail_name       = EXCLUDED.detail_name,
    summary_name      = EXCLUDED.summary_name,
    last_run_datetime = NOW(),
    description       = EXCLUDED.description;
