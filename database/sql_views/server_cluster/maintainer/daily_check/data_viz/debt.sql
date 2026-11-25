-- Daily data check — the UI's debt source: stats.debt_identity
-- ("one row per trading day", the UI latest-dates probe reads its
-- max(date), see data_viz/api/services/cache/latest_dates) vs the
-- index bars frontier. The debt family (PBoC OMO/repo/MLF, SHIBOR,
-- ChinaBond yields, LPR) is daily-frequency but parts publish on their
-- own clock (LPR monthly, some PBoC feeds next morning) — the
-- 4-calendar-day tolerance is the publish-lag slack; beyond it the
-- builds/bond pipeline stopped.
-- Serves data_viz.debt (DailyDataCheckJob,
-- server_cluster/maintainer/daily_check.py — zero rows = loaded; one
-- table-wide row when stale).
-- args: none
WITH frontier AS (
    SELECT MAX(date) AS frontier_date
    FROM stats.index_basic_stats
    WHERE close IS NOT NULL
),
built AS (
    SELECT MAX(date) AS latest_date
    FROM stats.debt_identity
)
SELECT NULL::text AS code, b.latest_date, f.frontier_date
FROM frontier f CROSS JOIN built b
WHERE b.latest_date IS NULL
   OR b.latest_date < f.frontier_date - INTERVAL '4 days'
