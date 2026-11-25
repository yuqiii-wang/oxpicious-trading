-- Daily data check — analysis_daily step 7 (analysis.analysis_composites):
-- analysis_composites.industry_corr_benchmark_offsets max(start_date)
-- vs the trailing 20-day window's start on the global index calendar
-- (min(WINDOWS) = 20 — the latest trailing window EVERY run refreshes;
-- the stride windows only materialize at their stride boundaries, so
-- the trailing start is the canary that advances daily). A lagging
-- max(start_date) means the nightly composites pass stopped (without
-- it the table went stale silently after the 2026-10-05 smallint
-- migration). STRICT (0-day): the trailing window re-targets every
-- nightly run, compared at 07:00 the morning after.
-- Serves analysis.analysis_composites (DailyDataCheckJob,
-- server_cluster/maintainer/daily_check.py — zero rows = loaded; one
-- table-wide row when stale).
-- args: none
WITH cal AS (
    SELECT date,
           ROW_NUMBER() OVER (ORDER BY date) - 1 AS idx,
           COUNT(*) OVER () AS n
    FROM (SELECT DISTINCT date
          FROM stats.index_basic_stats
          WHERE close IS NOT NULL) d
),
expected AS (
    SELECT date AS expected_start
    FROM cal
    WHERE idx = n - 20   -- the trailing 20-row window's start (0-based)
)
SELECT *
FROM (
    SELECT NULL::text AS code,
           (SELECT MAX(start_date)
            FROM analysis_composites.industry_corr_benchmark_offsets)
               AS latest_date,
           (SELECT MAX(date) FROM cal) AS frontier_date,
           (SELECT expected_start FROM expected) AS expected_start
) t
WHERE t.expected_start IS NOT NULL
  AND (t.latest_date IS NULL OR t.latest_date < t.expected_start)
