-- ============================================================================
--  Cross-Stats BENCHMARK WEIGHT SUMMARY — tiny per-(sec_type, benchmark_code,
--  code) rollup of the LATEST code_sec_shared_weight in stats.cross_stats:
--
--    • sec-alloc-live attribution weighted aggregates (data_viz
--      sec-alloc-live-attribution.service.ts, the cs_weight CTE): every
--      member's composition-overlap weight vs the selected benchmark. The
--      former live query — DISTINCT ON (code) … ORDER BY code, date DESC
--      over the 100M-row hash-partitioned main table filtered by
--      benchmark_code — had no index led by benchmark_code (only the
--      code-led PK + (sec_type, date)) and code is the HASH partition key,
--      so EVERY request seq-scanned all 8 partitions (~28s measured
--      2026-10-30; the endpoint fires per page load, tick click, and 5-min
--      refresh). This rollup (~28K pair-grain rows) turns the read into a
--      PK-prefix lookup (milliseconds).
--
--  Snapshot-constant semantics: cross_stats materializes the LATEST
--  sec_composition snapshot per code for ALL dates, so code_sec_shared_weight
--  is constant across a pair's dates — "latest row per pair" reproduces the
--  former live query exactly.
--
--  MAINTENANCE (builds.cross_stats, _summary.py — the same hooks as
--  15_cross_stats_code_summary.sql):
--    • After every data-writing run (pair/industry grain INSERT).
--    • On the incremental no-op path ONLY when stale: rollup empty while the
--      pair grain has rows (first deployment), pair grain gone after a
--      force truncate without a rewrite, or rollup MAX(last_date) <
--      dates-map MAX(date) (a newly written date is never registered
--      before its pair rows land).
--    • --corr upserts corr columns onto existing rows only → weights never
--      change → no refresh.
--  Full DELETE + recompute so removed pairs (force-mode recompute,
--  composition edits) never linger; the re-INSERT is ON CONFLICT DO
--  UPDATE-armored exactly like the code summary (the aggregate scans the
--  100M-row main table while the table sits DELETEd-but-uncommitted — a
--  concurrent summary writer committing a key in that window would
--  otherwise kill the run with UniqueViolationError).
--
--  CONSISTENCY: the API keeps the former live aggregate as a fallback when
--  no rollup rows exist for the requested (sec_type, benchmark_code) — a
--  fresh DB before the first builds.cross_stats run still renders.
-- ============================================================================

CREATE TABLE IF NOT EXISTS stats.cross_stats_benchmark_weight_summary (
    sec_type               TEXT NOT NULL,  -- grain discriminator (mirrors cross_stats.sec_type)
    benchmark_code         TEXT NOT NULL,  -- benchmark index code
    code                   TEXT NOT NULL,  -- subject code (index code / industry_id)
    last_date              DATE NOT NULL,  -- the pair's latest cross_stats date (staleness probe)
    code_sec_shared_weight NUMERIC(8,4),   -- verbatim latest weight (NULL passthrough)

    CONSTRAINT pk_cross_stats_benchmark_weight_summary
        PRIMARY KEY (sec_type, benchmark_code, code)
);

COMMENT ON TABLE stats.cross_stats_benchmark_weight_summary IS
  'Per-(sec_type, benchmark_code, code) rollup of the LATEST '
  'code_sec_shared_weight in stats.cross_stats. Rebuild-avoidance cache for '
  'the sec-alloc-live attribution weighted aggregates — replaces the '
  'per-request DISTINCT ON over the 100M-row hash-partitioned main table '
  '(~28s → ms). Refreshed by builds.cross_stats (_summary.py) at the same '
  'hooks as cross_stats_code_summary; the API falls back to the live '
  'aggregate when no rollup rows exist for the requested benchmark.';
COMMENT ON COLUMN stats.cross_stats_benchmark_weight_summary.last_date IS
  'The pair''s latest stats.cross_stats date at refresh time — the '
  'staleness probe compares MAX(last_date) against the dates map '
  '(stats.cross_stats_dates), mirroring cross_stats_code_summary.last_date.';
COMMENT ON COLUMN stats.cross_stats_benchmark_weight_summary.code_sec_shared_weight IS
  'The pair''s latest code_sec_shared_weight, verbatim (composition-'
  'snapshot-constant across the pair''s dates). NULL passthrough — '
  'consumers COALESCE to 0 (zero-overlap / unknown members contribute '
  'nothing to the weighted aggregate; NaN numerics are guarded at the '
  'consumer).';
