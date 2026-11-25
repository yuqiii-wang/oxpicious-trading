-- Daily data check — the UI's options source: stats.options_identity
-- (the (date, code) axis the UI's latest-dates probe reads, see
-- data_viz/api/services/cache/latest_dates) vs the index bars frontier
-- (the A-share trading calendar's latest day). Table-wide max vs max:
-- SSE/SZSE/CFFEX options settle on the same trading calendar and load
-- in the nightly chain — a table max behind the frontier (1-day slack
-- for the settlement files' late-afternoon publish) means the options
-- downloads or the identity build stopped.
-- Serves data_viz.options (DailyDataCheckJob,
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
    FROM stats.options_identity
)
SELECT NULL::text AS code, b.latest_date, f.frontier_date
FROM frontier f CROSS JOIN built b
WHERE b.latest_date IS NULL
   OR b.latest_date < f.frontier_date - INTERVAL '1 day'
