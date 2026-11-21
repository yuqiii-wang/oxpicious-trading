-- Non-daily check — the weekly SSE stock-PE anchor crawl's own last
-- success: the tier's in-task gate reads the same monitor.service_audit
-- clock (>= WEEKLY_GATE_DAYS since the last normal exit), so the
-- Saturday re-run only actually fires when the sweep is genuinely stale
-- (missed nightly fires all week; the checker applies its max_age_days
-- threshold to age_days). Serves downloads.stock.sse.trend_pe
-- (NonDailyOps, server_cluster/monitor/non_daily.py).
-- args: none
SELECT COUNT(*) AS n_rows,
       COALESCE(EXTRACT(EPOCH FROM (now() - MAX(ended_at)))
                / 86400.0, 1000000000) AS age_days
  FROM monitor.service_audit
 WHERE service = 'downloads.stock.sse.trend_pe'
   AND is_normal_exit
   AND ended_at IS NOT NULL
