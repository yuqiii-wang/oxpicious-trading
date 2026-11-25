-- Daily data check — the UI's etf_margin source: stats.etf_identity
-- (the (date, code) axis the UI's latest-dates probe reads, see
-- data_viz/api/services/cache/latest_dates) vs the index bars frontier
-- (the A-share trading calendar's latest day). Table-wide max vs max:
-- the ETF bars family is loaded by the nightly chain on the same
-- calendar, so a table max behind the frontier (1-day slack for
-- publish-order noise) means the ETF downloads or the identity build
-- stopped.
-- Serves data_viz.etf_margin (DailyDataCheckJob,
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
    FROM stats.etf_identity
)
SELECT NULL::text AS code, b.latest_date, f.frontier_date
FROM frontier f CROSS JOIN built b
WHERE b.latest_date IS NULL
   OR b.latest_date < f.frontier_date - INTERVAL '1 day'
