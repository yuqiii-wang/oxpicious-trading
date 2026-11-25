-- Daily data check — analysis_daily step 1 (analysis.market_beta):
-- the industry attribution dates vs the stats.cross_stats
-- (sec_type = 'industry') frontier — the module's own
-- find_missing_attribution_dates frontier (analysis/market_beta/
-- attributions/queries.py). The nightly producer/fill pass writes
-- every frontier date into analysis.industry_attributions; one or
-- more missing dates means it stopped writing. STRICT (0-day): both
-- sides advance in the same nightly pass, compared at 07:00 the
-- morning after.
-- Serves analysis.market_beta (DailyDataCheckJob,
-- server_cluster/maintainer/daily_check.py — zero rows = loaded; one
-- table-wide row when stale).
-- args: none
WITH frontier AS (
    SELECT DISTINCT date
    FROM stats.cross_stats
    WHERE sec_type = 'industry'
),
built AS (
    SELECT DISTINCT date
    FROM analysis.industry_attributions
),
diff AS (
    SELECT COUNT(*) AS missing_dates
    FROM (SELECT date FROM frontier
          EXCEPT
          SELECT date FROM built) m
)
SELECT NULL::text AS code,
       (SELECT MAX(date) FROM built) AS latest_date,
       (SELECT MAX(date) FROM frontier) AS frontier_date,
       d.missing_dates
FROM diff d
WHERE d.missing_dates > 0
