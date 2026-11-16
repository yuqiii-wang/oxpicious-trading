-- Non-daily check — stock dividend data freshness across ALL sources
-- (the load-side twin of stock_dividends_fresh: builds.stock.dividends
-- consumes both exchanges' CSVs, so the build is stale only when BOTH
-- sources' last_updates age out — the checker applies its max_age_days
-- threshold to age_days). Serves builds.stock.dividends (NonDailyOps,
-- server_cluster/monitor/non_daily.py).
-- args: none
SELECT COUNT(*) AS n_rows,
       MAX(last_updated) AS last_updated_at,
       COALESCE(EXTRACT(EPOCH FROM (now() - MAX(last_updated)))
                / 86400.0, 1000000000) AS age_days
  FROM stats.stock_dividends
