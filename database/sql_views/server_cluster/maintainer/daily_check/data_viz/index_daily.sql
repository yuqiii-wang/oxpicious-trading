-- Daily data check — the UI's index_daily source: stats.index_identity
-- (the (date, code) axis the UI's latest-dates probe reads, see
-- data_viz/api/services/cache/latest_dates) vs the bars frontier
-- (stats.index_basic_stats close IS NOT NULL — the canonical "latest
-- trading day with bars loaded", the oracle_signals_coverage
-- convention).
-- TWO failure modes, two verdict shapes (unioned; zero rows = loaded):
-- (1) FRONTIER-DAY COVERAGE (table-wide row, code NULL): the frontier
--     day's loaded-code count vs the PREVIOUS trading day's — a
--     frontier day carrying a fraction of the previous day's codes is
--     a PARTIAL load, invisible to any MAX(date) comparison (both
--     axes share the max date; the UI's own latest-dates probe reads
--     the same MAX from the identity table and false-clears too —
--     the 2026-11-20 stub: 2 of 515 codes with close, yet every
--     MAX-date check said loaded). Threshold 0.9 of the previous
--     day's coverage: legitimately thin days (2026-11-12's 381 of
--     523) alarm ON PURPOSE — a partial frontier IS a data hole the
--     owner studies (the 2026-10-17 partial-frontier precedent); a
--     frontier day with no previous trading day (first day ever) is
--     not judgeable and passes.
-- (2) IDENTITY LAG (per-code rows): a code whose newest identity row
--     is behind its own bars frontier — the nightly identity build
--     missed the night's bars, or a partial load — leaves the UI's
--     cached-data freshness probe stale for that code. STRICT
--     (0-day): identity and bars are built by the same nightly pass
--     and are compared at 07:00 the morning after.
-- Serves data_viz.index_daily (DailyDataCheckJob,
-- server_cluster/maintainer/daily_check.py — zero rows = loaded; one
-- row per stale sub-item).
-- args: none
WITH frontier AS (
    SELECT MAX(date) AS frontier_date
    FROM stats.index_basic_stats
    WHERE close IS NOT NULL
),
prev_day AS (
    SELECT MAX(date) AS prev_date
    FROM stats.index_basic_stats
    WHERE close IS NOT NULL
      AND date < (SELECT frontier_date FROM frontier)
),
coverage AS (
    SELECT
        (SELECT COUNT(DISTINCT code)
         FROM stats.index_basic_stats
         WHERE close IS NOT NULL
           AND date = (SELECT frontier_date FROM frontier)) AS covered,
        (SELECT COUNT(DISTINCT code)
         FROM stats.index_basic_stats
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
    FROM stats.index_basic_stats
    WHERE close IS NOT NULL
    GROUP BY code
) f
LEFT JOIN (
    SELECT code, MAX(date) AS latest_date
    FROM stats.index_identity
    GROUP BY code
) c ON c.code = f.code
WHERE c.latest_date IS NULL OR c.latest_date < f.frontier_date
