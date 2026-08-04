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
--    large. mean_price and var_price are computed across these rebased-to-100
--    values.
--
--    mean_pe and total_trading_amount are computed on RAW values (no rebasing):
--      • PE is already a ratio (scale-invariant) — rebasing would lose the
--        absolute valuation level (you want to see 15x vs 30x, not 100 vs 200).
--        mean_pe = AVG(raw PE) across member indices, NULL PE excluded.
--      • total_trading_amount = SUM(stock_basic_stats.trading_amount) across the UNION of
--        stocks from all member indices' active compositions. This captures
--        total industry capital flow (yuan), counting each stock ONCE even if
--        it appears in multiple member indices. Stock trading_amount source:
--        stats.stock_basic_stats.trading_amount (in yuan, converted from source CSV
--        成交金额(万元) × 10000).
--
--    NOTE: the rebased-to-100 ANCHOR for mean_price/var_price is the START OF
--    ALL HISTORY (fixed server-side). The frontend multi-line plot uses a
--    CLIENT-SIDE slider that re-rebases the LINES to the slider's window-start
--    — so the mean/var overlay and the lines are aligned only when the slider
--    is at full range. When the slider narrows, the lines re-rebase but the
--    mean/var overlay stays anchored at history start. This tradeoff was
--    chosen by the user: server-side precompute is cleanest with a single
--    fixed anchor.
--
--    LATEST-SNAPSHOT-ONLY (no temporal filter): stock_num and the stock
--    union for total_trading_amount are looked up from the LATEST
--    sec_composition snapshot per index code, regardless of the row's date.
--    This is a temporal extrapolation — the current composition is used as
--    a proxy for historical membership. Rationale: sec_composition only has
--    a few recent snapshots (from 2026-06-30 onward); filtering on
--    snapshot_date <= date would drop ALL pre-snapshot dates, leaving
--    non-all pool_size slices and total_trading_amount with only ~24 days
--    of history. Using the latest snapshot gives full history. The trading
--    VALUES (close, PE, trading_amount) are all historical — only the
--    stock UNIVERSE and pool_size classification use the latest snapshot.
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
--    stats.index_basic_stats.trading_amount   (raw daily trading_amount, yuan)
--    stats.index_valuation.pe         (raw daily PE ratio)
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
--    (date, industry_id, pool_size), aggregates across member indices in that
--    slice: mean_price + var_price (on rebased-to-100 closes), mean_pe (on
--    raw PE), total_trading_amount (SUM of stock trading_amount values via union of member
--    index compositions). index_count = number of member indices with close
--    data contributing on that date (PE means may be computed over fewer
--    indices when some lack valuation data).
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

    -- Cross-sectional MEAN of rebased-to-100 close values across member indices
    -- in this slice on this date. 100 = members flat vs history start.
    mean_price                NUMERIC(12,6),

    -- Cross-sectional VARIANCE of rebased-to-100 close values across member
    -- indices in this slice on this date. Captures cross-index dispersion
    -- (how spread out the member indices are on this date).
    var_price                 NUMERIC(20,6),

    -- Cross-sectional MEAN of raw PE (stats.index_valuation.pe) across member
    -- indices in this slice on this date. NULL PE values excluded. NULL when
    -- no member indices have PE data on this date.
    mean_pe                   NUMERIC(12,6),

    -- Total trading_amount (yuan): SUM of stats.stock_basic_stats.trading_amount across
    -- the UNION of stocks from all member indices' active compositions. Each
    -- stock counted ONCE (union, not sum-per-index). NULL when no stock trading_amount
    -- data is available for the union set on this date.
    total_trading_amount      NUMERIC(24,4),

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

COMMENT ON TABLE  analysis.industry_sentiments                IS 'Industry sentiment cross-section: one row per (date, industry_id, pool_size). Aggregates index values across member indices (stats.sec_classification type=''index'' AND industry_id matches AND index has composition data in stats.sec_composition source_type=''index'') in the named pool_size slice. Indices WITHOUT composition data are excluded entirely. mean_price/var_price: rebased-to-100 at each index''s first available close (history start). mean_pe: raw PE from stats.index_valuation. total_trading_amount: SUM of stock_basic_stats.trading_amount across the UNION of stocks from member indices'' active compositions (yuan). pool_size: small (stock_num<51), mid (51-180), large (>180), all (every compositioned member). Broad-market industries BROAD_CSI/BROAD_SSE/BROAD_SZSE/BROAD_STAR are aggregated identically. Built by analyze_industry_sentiments.py (truncate-then-recompute).';
COMMENT ON COLUMN analysis.industry_sentiments.pool_size      IS 'Pool-size slice: small (stock_num<51), mid (51-180), large (>180), all (every member). Classification source: stats.sec_composition (LATEST snapshot per code, no temporal filter — same composition used for all dates).';
COMMENT ON COLUMN analysis.industry_sentiments.index_count    IS 'Number of distinct member indices with close data contributing to this (date, industry_id, pool_size) slice on this date.';
COMMENT ON COLUMN analysis.industry_sentiments.mean_price     IS 'AVG(rebased_to_100 close) across member indices in this pool_size slice on this date. Rebased-to-100 at each index''s first available close (history start). 100 = members flat vs their history-start value.';
COMMENT ON COLUMN analysis.industry_sentiments.var_price      IS 'VARIANCE(rebased_to_100 close) across member indices in this pool_size slice on this date. Captures cross-index dispersion (how spread out the members are).';
COMMENT ON COLUMN analysis.industry_sentiments.mean_pe        IS 'AVG(raw PE) across member indices in this pool_size slice on this date. Source: stats.index_valuation.pe. NULL PE values excluded from the mean. NULL when no member indices have PE data on this date.';
COMMENT ON COLUMN analysis.industry_sentiments.total_trading_amount IS 'SUM(stock_basic_stats.trading_amount) across the UNION of stocks from all member indices'' compositions (LATEST sec_composition snapshot per code, no temporal filter — same stock universe for all dates) in this pool_size slice on this date. Each stock counted ONCE (union, not sum-per-index). Source: stats.stock_basic_stats.trading_amount (in yuan). NULL when no stock trading_amount data is available for the union set on this date.';

-- ============================================================================
--  Industry Correlations — pairwise rolling Pearson correlation of the
--  MEAN rebased-to-100 price series between two industries, bucketed by pool_size.
--
--  Table: analysis.industry_correlations
--    PK: (date, industry_id, benchmark_industry_id, pool_size)
--
--  SOURCE
--    analysis.industry_sentiments.mean_price     (per-industry mean price series)
--
--  CONVENTION
--    For each pair of industries (A, B) and each pool_size slice P, this
--    table stores the rolling-window Pearson correlation between A's
--    mean_rebased series and B's mean_rebased series on each date where the
--    two industries share enough overlapping history. Both industries are
--    compared in the SAME pool_size slice — a single `pool_size` column
--    captures this (the previous schema carried a redundant
--    `benchmark_industry_pool_size` that was always equal to
--    `industry_pool_size` via a CHECK constraint).
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
    pool_size                         TEXT          NOT NULL,
    date                              DATE          NOT NULL,

    -- Rolling Pearson correlation between the two industries' mean_rebased
    -- series over the named window ending on `date`. NULL when insufficient
    -- overlap (< window days) on or before `date`.
    industry_mean_corr_5d             NUMERIC(8,4),
    industry_mean_corr_20d            NUMERIC(8,4),
    industry_mean_corr_60d            NUMERIC(8,4),
    industry_mean_corr_255d           NUMERIC(8,4),

    CONSTRAINT pk_industry_correlations PRIMARY KEY
        (date, industry_id, benchmark_industry_id, pool_size),
    CONSTRAINT chk_industry_correlations_pool
        CHECK (pool_size IN ('small', 'mid', 'large', 'all')),
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
    (industry_id, benchmark_industry_id, pool_size, date);
CREATE INDEX IF NOT EXISTS idx_industry_correlations_bench_pair_pool_date
    ON analysis.industry_correlations
    (benchmark_industry_id, industry_id, pool_size, date);
CREATE INDEX IF NOT EXISTS idx_industry_correlations_date
    ON analysis.industry_correlations (date);

COMMENT ON TABLE  analysis.industry_correlations                            IS 'Pairwise rolling Pearson correlation between two industries'' mean_rebased series (analysis.industry_sentiments.mean_rebased). One row per (date, industry_id, benchmark_industry_id, pool_size). Both industries are compared in the SAME pool_size slice (single pool_size column). Self-pairs (A=B) excluded. Order convention: industry_id < benchmark_industry_id (lexicographic) to deduplicate (A,B) vs (B,A). Only same-pool slices materialized (all, small, mid, large). Built by analyze_industry_correlations.py (truncate-then-recompute).';
COMMENT ON COLUMN analysis.industry_correlations.industry_id                IS 'Subject industry''s industry_id (lexicographically smaller of the two).';
COMMENT ON COLUMN analysis.industry_correlations.benchmark_industry_id       IS 'Benchmark industry''s industry_id (lexicographically larger of the two).';
COMMENT ON COLUMN analysis.industry_correlations.pool_size                  IS 'Pool_size slice in which BOTH industries are compared (cross-pool comparisons are not materialized). small (stock_num<51), mid (51-180), large (>180), all (every member).';
COMMENT ON COLUMN analysis.industry_correlations.date                       IS 'End date of the rolling correlation window.';
COMMENT ON COLUMN analysis.industry_correlations.industry_mean_corr_5d      IS 'Pearson correlation between the two industries'' mean_rebased series over the trailing 5 trading days ending on `date`. NULL when < 5 overlapping days on or before `date`.';
COMMENT ON COLUMN analysis.industry_correlations.industry_mean_corr_20d     IS 'Pearson correlation between the two industries'' mean_rebased series over the trailing 20 trading days ending on `date`. NULL when < 20 overlapping days on or before `date`.';
COMMENT ON COLUMN analysis.industry_correlations.industry_mean_corr_60d     IS 'Pearson correlation between the two industries'' mean_rebased series over the trailing 60 trading days ending on `date`. NULL when < 60 overlapping days on or before `date`.';
COMMENT ON COLUMN analysis.industry_correlations.industry_mean_corr_255d    IS 'Pearson correlation between the two industries'' mean_rebased series over the trailing 255 trading days ending on `date`. NULL when < 255 overlapping days on or before `date`.';


-- ============================================================================
--  Industry Attributions — composition overlap between each industry (as a
--  group of member indices) and each benchmark index, aggregated to the
--  industry level.
--
--  Table: analysis.industry_attributions
--    PK: (date, industry_id, benchmark_code)
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
--    industry_sentiments). The shared-weight VALUES are constant per
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
--    analyze.industry_sentiments.attributions (internal step
--    run_attributions, invoked from __main__ after correlations). Depends on
--    analysis.sec_alloc_perf_attribution being populated first (by
--    analyze.sec_alloc_perf_attribution). Truncate-then-recompute on every run.
--
--  Register in analysis.analysis_identity (name='industry_attributions').
-- ============================================================================
DROP TABLE IF EXISTS analysis.industry_attributions;

CREATE TABLE IF NOT EXISTS analysis.industry_attributions (
    industry_id                       TEXT          NOT NULL,
    benchmark_code                    TEXT          NOT NULL,
    date                              DATE          NOT NULL,

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
    --   rolling_price  = 100 × cumprod(1 + non_industry_return) from the
    --                    benchmark's first available close. Shows the
    --                    cumulative performance of the non-shared portion.
    --   trading_amt    = benchmark.trading_amount - SUM(shared_stock.trading_amount).
    --                    The benchmark's turnover excluding the shared stocks.
    benchmark_non_this_industry_price      NUMERIC(20,4),
    benchmark_non_this_industry_rolling_price      NUMERIC(20,4),
    benchmark_non_this_industry_trading_amt NUMERIC(20,4),

    CONSTRAINT pk_industry_attributions PRIMARY KEY
        (date, industry_id, benchmark_code)
);

-- Indexes:
--   1. Per-industry + benchmark time series (drives the chart).
--   2. Per-date snapshot (drives the latest-date industry list).
CREATE INDEX IF NOT EXISTS idx_industry_attributions_industry_bench_date
    ON analysis.industry_attributions (industry_id, benchmark_code, date);
CREATE INDEX IF NOT EXISTS idx_industry_attributions_date
    ON analysis.industry_attributions (date);

COMMENT ON TABLE  analysis.industry_attributions                  IS 'Composition overlap between each industry (group of member indices) and each benchmark index. One row per (date, industry_id, benchmark_code). HYBRID aggregation: industry_shared_weight = SUM(code_sec_shared_weight) across member indices from analysis.sec_alloc_perf_attribution (each member contributes its OWN weight on shared stocks; can exceed 100; self-pairs excluded). benchmark_shared_weight = benchmark weight on the UNION of industry member stocks from stats.sec_composition (latest snapshot; bounded [0, 100] (percent); no double-counting). Recomputed from sec_composition (NOT summed from sec_alloc_perf_attribution) because a naive SUM of benchmark_sec_shared_weight across members would double-count stocks held by multiple members. Both columns use the LATEST sec_composition snapshot for all dates (temporal extrapolation). Built by analyze.industry_sentiments.attributions (internal step, truncate-then-recompute). Depends on analysis.sec_alloc_perf_attribution being populated first.';
COMMENT ON COLUMN analysis.industry_attributions.industry_id           IS 'Subject industry_id (from stats.sec_classification type=''index''). The industry whose member indices'' overlap with the benchmark is aggregated.';
COMMENT ON COLUMN analysis.industry_attributions.benchmark_code        IS 'Benchmark index code (typically one of the 6 broad-market indices: 000300, 000001, 000852, 399001, 399006, 000688, plus per-industry top-N non-broad indices). Inherited from analysis.sec_alloc_perf_attribution.benchmark_code.';
COMMENT ON COLUMN analysis.industry_attributions.date                  IS 'Trading date. Inherited from analysis.sec_alloc_perf_attribution (dates where at least one member index has a row for the benchmark). Shared-weight values are constant per (industry_id, benchmark_code) across dates, but the sum can vary when member indices have unequal history lengths.';
COMMENT ON COLUMN analysis.industry_attributions.industry_shared_weight  IS 'SUM(code_sec_shared_weight) across member indices in the industry, from analysis.sec_alloc_perf_attribution (sec_type=''index''). Each member contributes its OWN weight on stocks shared with the benchmark. Can exceed 100 (sum of multiple member portfolios — expected, NOT double-counting). Self-pairs (member == benchmark) excluded by sec_alloc_perf_attribution. NULL when the benchmark has no composition data (all members'' code_sec_shared_weight are NULL).';
COMMENT ON COLUMN analysis.industry_attributions.benchmark_shared_weight IS 'SUM(benchmark weight_pct) on the UNION of stocks held by ANY industry member (latest stats.sec_composition snapshot, source_type=''index''). Each stock counted ONCE (union) -> no double-counting. Bounded [0, 100] (weight_pct is stored as a percent, 0-100, not a fraction): fraction of the benchmark''s weight in the industry''s stock union. NULL when the benchmark has no composition data; 0 when the benchmark has composition but no overlap with the industry''s stocks. Recomputed from sec_composition (NOT summed from sec_alloc_perf_attribution) to avoid double-counting stocks held by multiple members.';
COMMENT ON COLUMN analysis.industry_attributions.benchmark_non_this_industry_price IS 'Benchmark close on the date with industry-shared stocks removed (today snapshot). Computed ONLY for broad-market benchmarks (is_broad_market=TRUE); NULL otherwise. Return-based decomposition: non_industry_return = (bench_return - swf × shared_portfolio_return) / (1 - swf), where swf = benchmark_shared_weight/100. price = bench_prev_close × (1 + non_industry_return). Shows what today''s close would be if only non-shared stocks moved today.';
COMMENT ON COLUMN analysis.industry_attributions.benchmark_non_this_industry_rolling_price IS 'Accumulated non-industry benchmark price, rebased to 100 at the benchmark''s first available close. Computed ONLY for broad-market benchmarks. = 100 × cumprod(1 + non_industry_return) from benchmark start. Shows the cumulative performance of the benchmark''s non-shared portion. When above the benchmark close, the industry was a drag; when below, the industry was a boost.';
COMMENT ON COLUMN analysis.industry_attributions.benchmark_non_this_industry_trading_amt IS 'Benchmark trading_amount on the date minus SUM of shared stocks'' trading_amount on the date. Computed ONLY for broad-market benchmarks. Shows the benchmark''s turnover excluding the industry''s shared stocks. NULL when the benchmark has no trading_amount data or no shared stock data on that date.';

-- ----------------------------------------------------------------------------
--  Register in analysis.analysis_identity
-- ----------------------------------------------------------------------------
INSERT INTO analysis.analysis_identity (name, detail_name, summary_name, last_run_datetime, description) VALUES
    ('industry_attributions', 'industry_attributions', NULL, NOW(),
     'Composition overlap between each industry (group of member indices) and each benchmark index. One row per (date, industry_id, benchmark_code). HYBRID aggregation: industry_shared_weight = SUM(code_sec_shared_weight) across member indices from analysis.sec_alloc_perf_attribution (own-weight on shared stocks in percent, can exceed 100, self-pairs excluded); benchmark_shared_weight = benchmark weight on the UNION of industry member stocks from stats.sec_composition (latest snapshot, in percent 0-100, no double-counting, recomputed from compositions to avoid double-counting stocks held by multiple members). Both use LATEST snapshot for all dates. Built by analyze.industry_sentiments.attributions (internal step, truncate-then-recompute). Depends on analysis.sec_alloc_perf_attribution being populated first.')
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
     'Pairwise rolling Pearson correlation between two industries'' mean_price series (analysis.industry_sentiments.mean_price). One row per (date, industry_id, benchmark_industry_id, pool_size) with corr_5d / corr_20d / corr_60d / corr_255d. Both industries are compared in the SAME pool_size slice (single pool_size column). Self-pairs (A=B) excluded. Order convention: industry_id < benchmark_industry_id to deduplicate. Only same-pool slices materialized (all, small, mid, large). Built by analyze_industry_correlations.py (truncate-then-recompute).')
ON CONFLICT (name) DO UPDATE SET
    detail_name       = EXCLUDED.detail_name,
    summary_name      = EXCLUDED.summary_name,
    last_run_datetime = NOW(),
    description       = EXCLUDED.description;
