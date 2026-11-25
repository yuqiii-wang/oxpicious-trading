-- Daily data check — the UI's sec_composition source:
-- stats.sec_composition max(snapshot_date) (the UI latest-dates probe,
-- see data_viz/api/services/cache/latest_dates) vs the index bars
-- frontier. Composition snapshots land monthly/quarterly (the CSI/SZSE
-- closeweight files), NOT daily — the 40-day tolerance is the snapshot
-- cadence slack; beyond it the composition builds stopped.
-- Serves data_viz.sec_composition (DailyDataCheckJob,
-- server_cluster/maintainer/daily_check.py — zero rows = loaded; one
-- table-wide row when stale).
-- args: none
WITH frontier AS (
    SELECT MAX(date) AS frontier_date
    FROM stats.index_basic_stats
    WHERE close IS NOT NULL
),
built AS (
    SELECT MAX(snapshot_date) AS latest_date
    FROM stats.sec_composition
)
SELECT NULL::text AS code, b.latest_date, f.frontier_date
FROM frontier f CROSS JOIN built b
WHERE b.latest_date IS NULL
   OR b.latest_date < f.frontier_date - INTERVAL '40 days'
