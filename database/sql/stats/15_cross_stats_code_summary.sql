-- ============================================================================
--  Cross-Stats CODE SUMMARY — tiny per-(sec_type, code) rollup of
--  stats.cross_stats that replaces per-request full-table aggregates on the
--  70M+ row main table:
--
--    • perf-attr codes list  (first/last date, n_dates, benchmarks array)
--    • perf-attr themes trees (DISTINCT code membership per sec_type)
--    • intraday-movements benchmark dropdown (benchmarks with pair history
--      = UNNEST(benchmarks) — a benchmark has history iff it appears in at
--      least one subject's benchmarks array)
--
--  A live GROUP BY code over stats.cross_stats costs ~30s per request
--  (hash-partitioned by code; COUNT(DISTINCT date) over 72M rows). This
--  rollup (~hundreds of rows) is rebuilt by the SAME build that writes the
--  main table, so the API reads it in milliseconds.
--
--  MAINTENANCE (builds.cross_stats)
--    • After every data-writing run (pair/industry grain INSERT) the runner
--      fully refreshes rows for the grains it hosts ('index', 'industry').
--    • On the incremental "up to date; nothing to do" early-return the
--      runner refreshes ONLY when stale (summary MAX(last_date) < map
--      MAX(date), or summary empty while the pair grain has rows).
--    • --corr never changes code membership/dates → no refresh.
--
--  CONSISTENCY
--    • Full DELETE + recompute (not upsert) so removed subjects/benchmarks
--      (force-mode recompute, composition edits) never linger.
--    • API consumers keep a live-aggregate fallback: when no summary rows
--      exist for the requested sec_type they probe stats.cross_stats and
--      fall back to the on-the-fly aggregate, so a fresh DB before the
--      first build still renders.
-- ============================================================================

CREATE TABLE IF NOT EXISTS stats.cross_stats_code_summary (
    sec_type    TEXT        NOT NULL,  -- grain discriminator (mirrors cross_stats.sec_type)
    code        TEXT        NOT NULL,  -- subject code (index code / industry_id)
    first_date  DATE        NOT NULL,  -- MIN(date) for the (sec_type, code)
    last_date   DATE        NOT NULL,  -- MAX(date)
    n_dates     INTEGER     NOT NULL,  -- COUNT(DISTINCT date)
    benchmarks  TEXT[]      NOT NULL DEFAULT '{}',  -- DISTINCT benchmark_code, ASC sorted

    CONSTRAINT pk_cross_stats_code_summary PRIMARY KEY (sec_type, code)
);

COMMENT ON TABLE  stats.cross_stats_code_summary IS 'Per-(sec_type, code) rollup of stats.cross_stats (first/last date, n_dates, DISTINCT benchmarks array). Rebuild-avoidance cache for API list endpoints; fully refreshed by builds.cross_stats after data-writing runs (and on the no-op path when stale). A benchmark has pair history iff it appears in at least one subject''s benchmarks array.';
COMMENT ON COLUMN stats.cross_stats_code_summary.n_dates IS 'COUNT(DISTINCT date) for the (sec_type, code) in stats.cross_stats.';
COMMENT ON COLUMN stats.cross_stats_code_summary.benchmarks IS 'DISTINCT benchmark_code values (ASC) ever paired with this code — UNNEST over all rows yields the full benchmark universe of the grain.';
