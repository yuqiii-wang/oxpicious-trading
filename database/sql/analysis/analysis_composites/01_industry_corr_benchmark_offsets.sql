-- ============================================================================
--  Industry Correlations by Benchmark Offset — opposite-industry audit.
--
--  Table: analysis_composites.industry_corr_benchmark_offsets
--    PK: (industry_id, benchmark_industry_id, pool_size, benchmark_code,
--         start_date, interval)
--
--  WHAT IT AUDITS
--    Raw industry-vs-industry correlations are dominated by the common
--    broad-market factor: when the benchmark moves, nearly every industry
--    composite moves with it, so pairwise MA-curve correlations
--    (analysis.industry_correlations) stay high even for industries whose
--    idiosyncratic trends are opposed. This table re-derives the pairwise
--    correlation AFTER each industry's trend is offset by the benchmark:
--    subtracting the (scaled) benchmark removes the common factor, so the
--    residual correlation isolates true co-movement / opposition.
--
--  MATH (per window, W in {20, 60, 255})
--    Trend curves (identical inputs to analysis.industry_correlations):
--      MA_X[t]  = trailing W-trading-day rolling mean of
--                 stats.industry_basic_stats.mean_close for industry X.
--      MA_B[t]  = trailing W-trading-day rolling mean of
--                 stats.index_basic_stats.close for benchmark_code,
--                 reindexed onto the pool calendar.
--    Per window starting at grid date s:
--      k_X      = MA_X[s] / MA_B[s]   (benchmark rebased to the
--                 industry's MA level at the window start — makes the
--                 offset scale-consistent: MA_B·k_X moves in the same
--                 units as MA_X).
--      Offset trend:           adj_X[t] = MA_X[t] − k_X · MA_B[t]
--        (benchmark REMOVED — an industry up while the benchmark is up
--        more is DOWN after the offset).
--      Recomputed price:       P_X[t]  = 100 + adj_X[t] − adj_X[s]
--        (the offset trend rebuilt as a price that starts at exactly 100
--        on the window's first day; Pearson is shift/scale-invariant, so
--        the rebase is presentation, not substance).
--    Audited metrics per pair (A, B) over the window's W dates:
--      overall_corr_ma{W}_{W}d     = Pearson(MA_A, MA_B) — the RAW
--        correlation, same value as analysis.industry_correlations
--        (self-contained audit baseline; the table never joins back).
--      offset_sub_corr_ma{W}_{W}d  = Pearson(P_A_sub, P_B_sub) —
--        correlation with the benchmark component REMOVED.
--      opposite_score_ma{W}_{W}d   = (1 − offset_sub_corr) / 2 ∈ [0, 1]
--        — the opposite-correlation score: 1.0 = perfectly opposite once
--        the benchmark is removed, 0.5 = uncorrelated residual,
--        0.0 = perfectly co-moving residual.
--
--  WINDOW SEMANTICS (identical grid to analysis.industry_correlations)
--    Window starts sit on the pool calendar grid: start indices 0,
--    interval, 2·interval, ... (interval default 20 trading days). The
--    window for *_ma{W}_{W}d spans the W trading days [start_date,
--    start_date + W). Only FULL windows are materialized:
--    overall_* requires both industries' MA-W defined on every window
--    date; offset_* additionally requires the benchmark's MA-W defined on
--    every window date. A window's value is final once its last date
--    exists, so rows are emitted exactly when start_date + W − 1 first
--    appears in the source. Self-pairs (A = B) are excluded (self-corr is
--    always 1). Rows are stored with industry_id <
--    benchmark_industry_id (lexicographic, COLLATE "C") to deduplicate
--    (A,B) vs (B,A). Both industries are compared in the SAME pool_size
--    slice (cross-pool comparisons are not materialized — see
--    05_industry_sentiments.sql for the rationale).
--
--  BENCHMARK
--    benchmark_code is part of the PK, so several offset benchmarks can
--    coexist. The default run materializes 000300 (CSI300); additional
--    benchmarks are added via `--benchmark CODE[,CODE...]`. Candidates are
--    broad-market indices (stats.sec_index_tags.is_broad_market = TRUE)
--    with full close history on the industry calendar.
--
--  SOURCE
--    stats.industry_basic_stats.mean_close   (industry composite close,
--                                            built by builds.industry)
--    stats.index_basic_stats.close           (benchmark index closes)
--
--  POPULATION
--    python -m analyze.analysis_composites
--      incremental: potential window END dates on the calendar grid not
--      yet covered by a computed window end are (re)upserted.
--      --force: truncate + full recompute.
--      --industry ID[,ID...] / --code CODE[,CODE...]: filtered mode —
--      recompute + upsert ALL windows for the pairs among the given
--      industries (driven by the UI refresh button). No truncate.
--
--  Register in analysis.analysis_identity
--  (name='industry_corr_benchmark_offsets').
-- ============================================================================
DROP TABLE IF EXISTS analysis_composites.industry_corr_benchmark_offsets;

CREATE TABLE IF NOT EXISTS analysis_composites.industry_corr_benchmark_offsets (
    industry_id                       TEXT          NOT NULL,
    benchmark_industry_id             TEXT          NOT NULL,
    pool_size                         TEXT          NOT NULL,
    benchmark_code                    TEXT          NOT NULL,
    start_date                        DATE          NOT NULL,
    "interval"                        INTEGER       NOT NULL DEFAULT 20,

    -- RAW pairwise Pearson correlation of the two industries' MA-{W}
    -- curves over the {W}-trading-day window starting on start_date.
    -- Same value as analysis.industry_correlations.corr_ma{W}_{W}d
    -- (same inputs, same grid — recomputed here so the audit row is
    -- self-contained). NULL when the window is not full.
    overall_corr_ma20_20d             NUMERIC(8,4),
    overall_corr_ma60_60d             NUMERIC(8,4),
    overall_corr_ma255_255d           NUMERIC(8,4),

    -- Pearson correlation of the RECOMPUTED PRICES with the benchmark
    -- SUBTRACTED (common market factor removed):
    -- P_X = 100 + (MA_X − k_X·MA_B) − (MA_X − k_X·MA_B)|_{t=s},
    -- k_X = MA_X[s] / MA_B[s]. NULL when the window is not full for
    -- either industry OR the benchmark.
    offset_sub_corr_ma20_20d          NUMERIC(8,4),
    offset_sub_corr_ma60_60d          NUMERIC(8,4),
    offset_sub_corr_ma255_255d        NUMERIC(8,4),

    -- Opposite-correlation score = (1 − offset_sub_corr) / 2 ∈ [0, 1].
    -- 1.0 = perfectly opposite after removing the benchmark component,
    -- 0.5 = uncorrelated residual, 0.0 = perfectly co-moving residual.
    -- Audited over diff periods: ma20/60/255 windows.
    opposite_score_ma20_20d           NUMERIC(8,4),
    opposite_score_ma60_60d           NUMERIC(8,4),
    opposite_score_ma255_255d         NUMERIC(8,4),

    -- No CHECK constraints (pool/interval enums + pair ordering): the
    -- builder guarantees these; per-row validation only slowed the bulk
    -- COPY writes (same convention as analysis.industry_correlations).
    CONSTRAINT pk_industry_corr_benchmark_offsets PRIMARY KEY
        (industry_id, benchmark_industry_id, pool_size, benchmark_code,
         start_date, "interval")
) PARTITION BY HASH (industry_id);

-- Native hash partitions (16) keyed by industry_id — created via the
-- shared util (database/sql/00_partition_utils.sql); children are named
-- _p00.._p15
SELECT public.create_hash_partitions('analysis_composites', 'industry_corr_benchmark_offsets', 16);

-- Indexes: none beyond the PK (same convention as
-- analysis.industry_correlations — per-pair chart queries are PK-prefix
-- scans; benchmark_code sits inside the PK for point filtering).

COMMENT ON TABLE  analysis_composites.industry_corr_benchmark_offsets              IS 'Opposite industry correlations by benchmark offset: pairwise Pearson correlation of two industries'' MA curves of mean_close audited over 20/60/255d windows, RAW (overall_corr_*) and after each industry''s trend is offset by a broad-market benchmark (industry MA minus rebased benchmark MA -> recomputed price starting at 100 -> offset_sub_corr_*; removes the common market factor), plus the derived opposite_score_* = (1 - offset_sub_corr)/2 in [0,1] (1 = perfectly opposite once the benchmark factor is removed). One row per (industry_id, benchmark_industry_id, pool_size, benchmark_code, start_date, interval). Window starts on the pool calendar grid every interval (default 20) trading days; window W spans [start_date, start_date + W). k_X = MA_X[s]/MA_B[s] rebases the benchmark to each industry''s MA level at the window start. Both industries compared in the SAME pool_size slice; self-pairs excluded; industry_id < benchmark_industry_id (lexicographic). Default benchmark 000300 (extra benchmarks via --benchmark). Sources: stats.industry_basic_stats.mean_close + stats.index_basic_stats.close. Built by python -m analyze.analysis_composites (incremental / --force / --industry filtered).';
COMMENT ON COLUMN analysis_composites.industry_corr_benchmark_offsets.industry_id           IS 'Subject industry''s industry_id (lexicographically smaller of the pair).';
COMMENT ON COLUMN analysis_composites.industry_corr_benchmark_offsets.benchmark_industry_id  IS 'Benchmark industry''s industry_id (lexicographically larger of the pair) — the OTHER industry of the pair, NOT the offset benchmark index (that is benchmark_code).';
COMMENT ON COLUMN analysis_composites.industry_corr_benchmark_offsets.pool_size             IS 'Pool_size slice in which BOTH industries are compared (cross-pool comparisons are not materialized). small (stock_num<51), mid (51-180), large (>180), all (every member).';
COMMENT ON COLUMN analysis_composites.industry_corr_benchmark_offsets.benchmark_code        IS 'Broad-market index code used for the trend offset (e.g. 000300 = CSI300). Part of the PK so several offset benchmarks can coexist. The benchmark''s MA-{W} close curve is rebased to each industry''s MA level at each window start before being subtracted.';
COMMENT ON COLUMN analysis_composites.industry_corr_benchmark_offsets.start_date            IS 'Start date of the compute window on the pool calendar grid (grid stride = interval trading days). The window for *_ma{W}_{W}d spans [start_date, start_date + W).';
COMMENT ON COLUMN analysis_composites.industry_corr_benchmark_offsets."interval"            IS 'Stride in trading days between consecutive window starts on the pool calendar grid (default 20).';
COMMENT ON COLUMN analysis_composites.industry_corr_benchmark_offsets.overall_corr_ma20_20d  IS 'RAW Pearson correlation between the two industries'' MA20 curves over the 20 trading days starting on start_date (same value as analysis.industry_correlations.corr_ma20_20d — recomputed here so the audit row is self-contained). NULL when the window is not full.';
COMMENT ON COLUMN analysis_composites.industry_corr_benchmark_offsets.overall_corr_ma60_60d  IS 'RAW Pearson correlation between the two industries'' MA60 curves over the 60 trading days starting on start_date. NULL when the window is not full.';
COMMENT ON COLUMN analysis_composites.industry_corr_benchmark_offsets.overall_corr_ma255_255d IS 'RAW Pearson correlation between the two industries'' MA255 curves over the 255 trading days starting on start_date. NULL when the window is not full.';
COMMENT ON COLUMN analysis_composites.industry_corr_benchmark_offsets.offset_sub_corr_ma20_20d  IS 'Pearson correlation of the benchmark-offset SUBTRACTED recomputed prices over the 20 trading days starting on start_date: P_X = 100 + (MA_X - k_X*MA_B) - value at window start, k_X = MA_X[s]/MA_B[s]. Removes the common market factor; residual co-movement / opposition. NULL when the window is not full for either industry or the benchmark.';
COMMENT ON COLUMN analysis_composites.industry_corr_benchmark_offsets.offset_sub_corr_ma60_60d  IS 'Pearson correlation of the benchmark-offset SUBTRACTED recomputed prices over the 60 trading days starting on start_date (see offset_sub_corr_ma20_20d). NULL when the window is not full.';
COMMENT ON COLUMN analysis_composites.industry_corr_benchmark_offsets.offset_sub_corr_ma255_255d IS 'Pearson correlation of the benchmark-offset SUBTRACTED recomputed prices over the 255 trading days starting on start_date (see offset_sub_corr_ma20_20d). NULL when the window is not full.';
COMMENT ON COLUMN analysis_composites.industry_corr_benchmark_offsets.opposite_score_ma20_20d   IS 'Opposite-correlation score over the 20d window = (1 - offset_sub_corr_ma20_20d) / 2, in [0, 1]. 1.0 = perfectly opposite after removing the benchmark component, 0.5 = uncorrelated residual, 0.0 = perfectly co-moving residual. NULL when offset_sub_corr is NULL.';
COMMENT ON COLUMN analysis_composites.industry_corr_benchmark_offsets.opposite_score_ma60_60d   IS 'Opposite-correlation score over the 60d window = (1 - offset_sub_corr_ma60_60d) / 2, in [0, 1] (see opposite_score_ma20_20d). NULL when offset_sub_corr is NULL.';
COMMENT ON COLUMN analysis_composites.industry_corr_benchmark_offsets.opposite_score_ma255_255d IS 'Opposite-correlation score over the 255d window = (1 - offset_sub_corr_ma255_255d) / 2, in [0, 1] (see opposite_score_ma20_20d). NULL when offset_sub_corr is NULL.';

-- ----------------------------------------------------------------------------
--  Register in analysis.analysis_identity
-- ----------------------------------------------------------------------------
INSERT INTO analysis.analysis_identity (name, detail_name, summary_name, last_run_datetime, description) VALUES
    ('industry_corr_benchmark_offsets', 'industry_corr_benchmark_offsets', NULL, NOW(),
     'Opposite industry correlations by benchmark offset: pairwise Pearson correlation of two industries'' MA curves of mean_close (stats.industry_basic_stats) audited over 20/60/255 trading-day windows, RAW (overall_corr_ma{W}_{W}d) and after each industry''s MA trend is offset by a broad-market benchmark — benchmark MA rebased to the industry''s MA level at each window start (k = MA_X[s]/MA_B[s]) and SUBTRACTED (common market factor removed), rebuilt as a recomputed price starting at 100 (offset_sub_corr_*), plus the derived opposite_score_ma{W}_{W}d = (1 - offset_sub_corr)/2 in [0,1] (1 = perfectly opposite once the benchmark factor is removed). One row per (industry_id, benchmark_industry_id, pool_size, benchmark_code, start_date, interval); window grid identical to analysis.industry_correlations (stride 20). Default benchmark 000300. Sources: stats.industry_basic_stats.mean_close + stats.index_basic_stats.close. Built by python -m analyze.analysis_composites (incremental / --force / --industry filtered).')
ON CONFLICT (name) DO UPDATE SET
    detail_name       = EXCLUDED.detail_name,
    summary_name      = EXCLUDED.summary_name,
    last_run_datetime = NOW(),
    description       = EXCLUDED.description;
