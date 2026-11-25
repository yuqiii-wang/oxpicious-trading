-- Daily data check — the UI's stock_daily source: stats.stock_identity
-- (the (date, code) axis the UI's latest-dates probe reads, see
-- data_viz/api/services/cache/latest_dates) vs the bars frontier
-- (stats.stock_basic_stats close IS NOT NULL — the canonical "latest
-- trading day with bars loaded", the oracle_signals_coverage
-- convention).
-- TWO failure modes, two verdict shapes (unioned; zero rows = loaded):
-- (1) FRONTIER-DAY COVERAGE (table-wide row, code NULL): the frontier
--     day's loaded-code count vs the PREVIOUS trading day's — a
--     frontier day carrying a fraction of the previous day's codes is
--     a PARTIAL load, invisible to any MAX(date) comparison (the
--     2026-11-20 index stub's shape; see data_viz/index_daily for the
--     full rationale). Threshold 0.9 of the previous day's coverage;
--     a frontier day with no previous trading day is not judgeable
--     and passes.
-- (2) IDENTITY LAG (per-code rows): a code whose newest identity row
--     is behind its own bars frontier — the nightly identity build
--     missed the night's bars, or a partial load — leaves the UI's
--     cached-data freshness probe stale for that code. STRICT
--     (0-day): identity and bars are built by the same nightly pass
--     and are compared at 07:00 the morning after.
-- Serves data_viz.stock_daily (DailyDataCheckJob,
-- server_cluster/maintainer/daily_check.py — zero rows = loaded; one
-- row per stale sub-item).
-- args: none
WITH frontier AS (
    SELECT MAX(date) AS frontier_date
    FROM stats.stock_basic_stats
    WHERE close IS NOT NULL
),
prev_day AS (
    SELECT MAX(date) AS prev_date
    FROM stats.stock_basic_stats
    WHERE close IS NOT NULL
      AND date < (SELECT frontier_date FROM frontier)
),
coverage AS (
    SELECT
        (SELECT COUNT(DISTINCT code)
         FROM stats.stock_basic_stats
         WHERE close IS NOT NULL
           AND date = (SELECT frontier_date FROM frontier)) AS covered,
        (SELECT COUNT(DISTINCT code)
         FROM stats.stock_basic_stats
         WHERE close IS NOT NULL
           AND date = (SELECT prev_date FROM prev_day)) AS expected
)
SELECT NULL::text AS code,
       NULL::date AS latest_date,
       f.frontier_date,
       c.covered,
       c.expected
FROM frontier f CROSS JOIN coverage c
WHERE c.expected IS NOT NULL
  AND c.covered < c.expected * 0.9
UNION ALL
SELECT f.code::text AS code,
       c.latest_date,
       f.frontier_date,
       NULL::bigint AS covered,
       NULL::bigint AS expected
FROM (
    SELECT code, MAX(date) AS frontier_date
    FROM stats.stock_basic_stats
    WHERE close IS NOT NULL
    GROUP BY code
) f
LEFT JOIN (
    SELECT code, MAX(date) AS latest_date
    FROM stats.stock_identity
    GROUP BY code
) c ON c.code = f.code
WHERE c.latest_date IS NULL OR c.latest_date < f.frontier_date
