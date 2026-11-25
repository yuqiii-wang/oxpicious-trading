-- Daily data check — analysis_daily step 10
-- (analysis.pair_pct_change_ratios): the table's max(date) vs the
-- stride-20 grid date(s) that have left the module's 15-calendar-day
-- re-target grace. The ratios materialize ONLY on the stride-20 grid
-- (every 20th trading day of the global index calendar — the
-- grid_dates view's own construction, mirrored here), so between grid
-- dates the table legitimately stands still: the latest grid date is
-- re-targeted while the frontier is within 15 calendar days of it and
-- FROZEN beyond. The verdict therefore demands only the grid dates
-- STRICTLY more than 15 days past the frontier (they left the grace
-- window long ago) to be covered — max(due grid date) = due_date; a
-- table max below it means the nightly run stopped writing.
-- Serves analysis.pair_pct_change_ratios (DailyDataCheckJob,
-- server_cluster/maintainer/daily_check.py — zero rows = loaded; one
-- table-wide row when stale).
-- args: none
WITH cal AS (
    SELECT DISTINCT date FROM stats.index_basic_stats
),
grid AS (
    SELECT date, ROW_NUMBER() OVER (ORDER BY date) - 1 AS idx
    FROM cal
),
due AS (
    SELECT MAX(date) AS due_date
    FROM grid
    WHERE idx % 20 = 19
      AND date < (SELECT MAX(date) FROM cal) - INTERVAL '15 days'
)
SELECT *
FROM (
    SELECT NULL::text AS code,
           (SELECT MAX(date) FROM analysis.pair_pct_change_ratios)
               AS latest_date,
           (SELECT MAX(date) FROM cal) AS frontier_date,
           (SELECT due_date FROM due) AS due_grid_date
) t
WHERE t.due_grid_date IS NOT NULL
  AND (t.latest_date IS NULL OR t.latest_date < t.due_grid_date)
