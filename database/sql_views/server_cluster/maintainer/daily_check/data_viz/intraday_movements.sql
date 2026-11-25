-- Daily data check — the UI's intraday_movements source:
-- stats.index_intraday_5min (close IS NOT NULL — the no-trade-bar
-- exclusion the UI latest-dates probe itself uses, see
-- data_viz/api/services/cache/latest_dates) vs the index bars frontier.
-- The 5-minute bars stream in from the always-on exchange streams
-- during the session; a table max behind the frontier (1-day slack)
-- means the stream pollers died and the UI's intraday pages went
-- stale.
-- Serves data_viz.intraday_movements (DailyDataCheckJob,
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
    FROM stats.index_intraday_5min
    WHERE close IS NOT NULL
)
SELECT NULL::text AS code, b.latest_date, f.frontier_date
FROM frontier f CROSS JOIN built b
WHERE b.latest_date IS NULL
   OR b.latest_date < f.frontier_date - INTERVAL '1 day'
