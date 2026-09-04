-- ============================================================================
--  Industry Basic Stats (Baseline) — cross-sectional aggregation of
--  REBASED-TO-100 index OHLC across member indices within each industry,
--  bucketed by pool_size. Renamed from analysis.industry_sentiments
--  (2026-08-24) and promoted to a STATS baseline table, mirroring
--  05_index_baseline.sql (stats.index_basic_stats) / 06_stock_baseline.sql
--  (stats.stock_basic_stats) conventions.
--
--  Table: stats.industry_basic_stats
--    PK: (industry_id, date, pool_size)
--    pool_size ∈ ('small','mid','large','all')
--      small = stock_num < 51    (tight thematic indices, e.g. 中证银行 50)
--      mid   = stock_num 51-180  (mid-cap baskets, e.g. CSI 100/200)
--      large = stock_num > 180   (broad baskets, e.g. CSI 300/500/800/1000)
--      all   = every member index regardless of pool size
--    One row per (date, industry_id, pool_size) slice stores the MEAN of the
--    rebased-to-100 OHLC values (the COMPOSITE index OHLC) across member
--    indices in that slice on that date.
--
--  REBASE CONVENTION (fixed at history start, scale-invariant)
--    Each member index is scaled by ONE per-index factor = 100 / first
--    available close (per-index first date — indices listed later start at
--    100 on their own first date). The SAME factor is applied to open /
--    high / low / close so the composite OHLC preserves each member's
--    intraday shape on a common close-anchored scale:
--      rebased_open  = open  × (100 / first_close)
--      rebased_high  = high  × (100 / first_close)
--      rebased_low   = low   × (100 / first_close)
--      rebased_close = close × (100 / first_close)
--    mean_open / mean_high / mean_low / mean_close are the cross-sectional
--    MEANs of these rebased values across member indices in the slice —
--    together they form the composite index OHLC. mean_close is the former
--    mean_price column (same rebased-to-100 close logic, rehooked).
--    Rows with NULL open/high/low (e.g. estimated-close rows) are excluded
--    from that field's mean only (pandas skipna semantics) — they still
--    contribute to mean_close / var_price.
--
--    mean_pe and total_trading_amount are computed on RAW values (no rebasing):
--      • PE is already a ratio (scale-invariant) — rebasing would lose the
--        absolute valuation level (you want to see 15x vs 30x, not 100 vs 200).
--        mean_pe = AVG(raw PE) across member indices, NULL PE excluded.
--      • total_trading_amount = SUM(stock_liquidity_margin.trading_amount) across the UNION of
--        stocks from all member indices' active compositions. This captures
--        total industry capital flow (yuan), counting each stock ONCE even if
--        it appears in multiple member indices. Stock trading_amount source:
--        stats.stock_liquidity_margin.trading_amount (in yuan, converted from source CSV
--        成交金额(万元) × 10000). Mirrors etf_liquidity_margin.trading_amount.
--
--    NOTE: the rebased-to-100 ANCHOR for the mean_* columns is the START OF
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
--    VALUES (OHLC, PE, trading_amount) are all historical — only the
--    stock UNIVERSE and pool_size classification use the latest snapshot.
--
--  BROAD-MARKET INDICES
--    Broad-market benchmarks (CSI 300, SSE 50, etc.) are classified in
--    stats.sec_classification under industry_ids BROAD_CSI, BROAD_SSE,
--    BROAD_SZSE, BROAD_STAR — they appear as 'industries' in the themes tree
--    and are aggregated IDENTICALLY to industry indices (no special handling).
--    The 'all' pool_size slice for BROAD_* industries gives the broad-market
--    aggregate.
--
--  SOURCE
--    stats.index_basic_stats.open/high/low/close  (raw daily index OHLC)
--    stats.index_valuation.pe                     (raw daily PE ratio)
--    JOIN stats.sec_classification  (type='index') for industry membership
--    stats.sec_composition          (stock_num → pool_size classification)
--
--  COMPOSITION-ONLY FILTER
--    Only indices that have at least one snapshot in stats.sec_composition
--    (source_type='index') are included. Indices WITHOUT any composition
--    data are dropped entirely — they contribute nothing to any pool_size
--    slice. pool_size classification is only meaningful for indices whose
--    member count is known, and the 'all' slice reflects compositioned
--    indices only.
--
--  DUMMY-INDEX FILTER
--    Synthetic DUMMY_* indices (stats.sec_classification.is_dummy = TRUE —
--    placeholder parents for orphan ETFs) are SKIPPED when empty, i.e. when
--    they carry no stats.index_basic_stats rows. They never do (they are
--    regenerated each classification build with no OHLC data), so in
--    practice all dummy indices are excluded; the guard keeps the intent
--    explicit should a dummy ever gain data.
--
--  POPULATION
--    builds.industry (python -m builds.industry) — incremental (missing
--    dates only, source stats.index_identity) or --force
--    (truncate-then-recompute). Rebase point is per-index first-available
--    close (history start). Per (date, industry_id, pool_size), aggregates
--    across member indices in that slice: mean_open/high/low/close (on
--    rebased-to-100 OHLC), var_price (on rebased-to-100 close — kept from
--    the former schema, cross-index dispersion), mean_pe (on raw PE),
--    total_trading_amount (SUM of stock trading_amount values via union of
--    member index compositions). index_count = number of member indices
--    with close data contributing on that date (PE means may be computed
--    over fewer indices when some lack valuation data).
-- ============================================================================

DROP TABLE IF EXISTS analysis.industry_sentiments;

-- Remove the stale analysis.analysis_identity row for the former
-- analysis.industry_sentiments table. The baseline is now a STATS table
-- owned by builds.industry — stats tables don't register in
-- analysis.analysis_identity (only the downstream analysis steps do:
-- industry_correlations, industry_attributions,
-- industry_etf_contribution, industry_hypes_and_drains).
DELETE FROM analysis.analysis_identity WHERE name = 'industry_sentiments';

CREATE TABLE IF NOT EXISTS stats.industry_basic_stats (
    date                      DATE          NOT NULL,
    industry_id               TEXT          NOT NULL,
    pool_size                 TEXT          NOT NULL,  -- 'small' | 'mid' | 'large' | 'all'

    -- Display label (denormalized)
    industry_label            TEXT          NOT NULL DEFAULT '',

    -- Number of member indices contributing to this (date, industry_id, pool_size) slice.
    index_count               INTEGER,

    -- Composite index OHLC: cross-sectional MEAN of rebased-to-100 OHLC
    -- values across member indices in this slice on this date. All four
    -- fields share the per-index scale factor 100 / first available close.
    -- 100 = members flat vs history start.
    mean_open                 NUMERIC(12,6),
    mean_high                 NUMERIC(12,6),
    mean_low                  NUMERIC(12,6),
    mean_close                NUMERIC(12,6),

    -- Cross-sectional VARIANCE of rebased-to-100 close values across member
    -- indices in this slice on this date. Captures cross-index dispersion
    -- (how spread out the member indices are on this date). Kept from the
    -- former industry_sentiments schema (non-mean_* logic unchanged).
    var_price                 NUMERIC(20,6),

    -- Cross-sectional MEAN of raw PE (stats.index_valuation.pe) across member
    -- indices in this slice on this date. NULL PE values excluded. NULL when
    -- no member indices have PE data on this date.
    mean_pe                   NUMERIC(12,6),

    -- Total trading_amount (yuan): SUM of stats.stock_liquidity_margin.trading_amount across
    -- the UNION of stocks from all member indices' active compositions. Each
    -- stock counted ONCE (union, not sum-per-index). NULL when no stock trading_amount
    -- data is available for the union set on this date.
    total_trading_amount      NUMERIC(24,4),

    CONSTRAINT pk_industry_basic_stats PRIMARY KEY (industry_id, date, pool_size),
    CONSTRAINT chk_industry_basic_stats_pool
        CHECK (pool_size IN ('small', 'mid', 'large', 'all'))
) PARTITION BY HASH (industry_id);

-- Native hash partitions (8) keyed by industry_id
-- Native hash partitions (8) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('stats', 'industry_basic_stats', 8);

-- Indexes for the common access patterns:
--   1. Per-industry + pool_size time series (drives the chart on the
--      IndustrySentiments page).
--   2. Per-date snapshot (drives the latest-date industries list).
CREATE INDEX IF NOT EXISTS idx_industry_basic_stats_industry_pool_date
    ON stats.industry_basic_stats (industry_id, pool_size, date);
CREATE INDEX IF NOT EXISTS idx_industry_basic_stats_date_industry
    ON stats.industry_basic_stats (date, industry_id);

COMMENT ON TABLE  stats.industry_basic_stats                IS 'Industry baseline cross-section: one row per (industry_id, date, pool_size). Aggregates index OHLC across member indices (stats.sec_classification type=''index'' AND industry_id matches AND index has composition data in stats.sec_composition source_type=''index'') in the named pool_size slice. Indices WITHOUT composition data are excluded entirely; synthetic DUMMY_* indices are skipped when empty. mean_open/high/low/close: composite index OHLC = cross-sectional mean of rebased-to-100 OHLC, single per-index scale factor 100 / first available close applied to all four fields (mean_close is the former mean_price). var_price: VARIANCE of rebased-to-100 close. mean_pe: raw PE from stats.index_valuation. total_trading_amount: SUM of stock_liquidity_margin.trading_amount across the UNION of stocks from member indices'' active compositions (yuan). pool_size: small (stock_num<51), mid (51-180), large (>180), all (every compositioned member). Broad-market industries BROAD_CSI/BROAD_SSE/BROAD_SZSE/BROAD_STAR are aggregated identically. Built by builds.industry (incremental / --force truncate-then-recompute).';
COMMENT ON COLUMN stats.industry_basic_stats.pool_size      IS 'Pool-size slice: small (stock_num<51), mid (51-180), large (>180), all (every member). Classification source: stats.sec_composition (LATEST snapshot per code, no temporal filter — same composition used for all dates).';
COMMENT ON COLUMN stats.industry_basic_stats.index_count    IS 'Number of distinct member indices with close data contributing to this (date, industry_id, pool_size) slice on this date.';
COMMENT ON COLUMN stats.industry_basic_stats.mean_open      IS 'AVG(rebased_to_100 open) across member indices in this pool_size slice on this date — composite index open. All OHLC fields share the per-index scale factor 100 / first available close (history start). Rows with NULL open are excluded from this mean only.';
COMMENT ON COLUMN stats.industry_basic_stats.mean_high      IS 'AVG(rebased_to_100 high) across member indices in this pool_size slice on this date — composite index high. All OHLC fields share the per-index scale factor 100 / first available close (history start). Rows with NULL high are excluded from this mean only.';
COMMENT ON COLUMN stats.industry_basic_stats.mean_low       IS 'AVG(rebased_to_100 low) across member indices in this pool_size slice on this date — composite index low. All OHLC fields share the per-index scale factor 100 / first available close (history start). Rows with NULL low are excluded from this mean only.';
COMMENT ON COLUMN stats.industry_basic_stats.mean_close     IS 'AVG(rebased_to_100 close) across member indices in this pool_size slice on this date — composite index close (former mean_price column, same logic). Rebased-to-100 at each index''s first available close (history start). 100 = members flat vs their history-start value.';
COMMENT ON COLUMN stats.industry_basic_stats.var_price      IS 'VARIANCE(rebased_to_100 close) across member indices in this pool_size slice on this date. Captures cross-index dispersion (how spread out the members are).';
COMMENT ON COLUMN stats.industry_basic_stats.mean_pe        IS 'AVG(raw PE) across member indices in this pool_size slice on this date. Source: stats.index_valuation.pe. NULL PE values excluded from the mean; PE = 0 is treated as a no-data marker and likewise excluded (never averaged in as 0). NULL when no member indices have PE data on this date.';
COMMENT ON COLUMN stats.industry_basic_stats.total_trading_amount IS 'SUM(stock_liquidity_margin.trading_amount) across the UNION of stocks from all member indices'' compositions (LATEST sec_composition snapshot per code, no temporal filter — same stock universe for all dates) in this pool_size slice on this date. Each stock counted ONCE (union, not sum-per-index). Source: stats.stock_liquidity_margin.trading_amount (in yuan). NULL when no stock trading_amount data is available for the union set on this date.';
