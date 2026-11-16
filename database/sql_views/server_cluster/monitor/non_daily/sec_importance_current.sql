-- Non-daily check — the monthly importance ranks' as-of freshness:
-- builds.classification.importance rebuilds stats.sec_importance_ranks
-- (truncate + rebuild) on each month's first run, so the table is
-- complete while max(as_of) is inside the current month (the checker
-- compares max_as_of < month_start). Serves
-- builds.classification.importance (NonDailyOps,
-- server_cluster/monitor/non_daily.py).
-- args: none
SELECT COUNT(*) AS n_rows,
       MAX(as_of) AS max_as_of,
       date_trunc('month', current_date)::date AS month_start
  FROM stats.sec_importance_ranks
