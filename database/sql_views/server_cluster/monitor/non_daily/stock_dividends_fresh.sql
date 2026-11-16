-- Non-daily check — stock dividend data freshness per source: the
-- downloads (fetch) + builds.stock.dividends (load) pair is complete
-- while the source's newest last_updated is inside the check window
-- (the checker applies its max_age_days threshold to age_days; a
-- source with zero rows is stale by construction). Serves
-- downloads.stock.{sse,szse}.dividend + builds.stock.dividends
-- (NonDailyOps, server_cluster/monitor/non_daily.py).
-- args:
--   source: value text — the stock_dividends source scope ('SSE' | 'SZSE')
SELECT COUNT(*) AS n_rows,
       MAX(last_updated) AS last_updated_at,
       COALESCE(EXTRACT(EPOCH FROM (now() - MAX(last_updated)))
                / 86400.0, 1000000000) AS age_days
  FROM stats.stock_dividends
 WHERE source = {{source}}::text
