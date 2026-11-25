-- Non-daily check — the weekly CSIndex index-PE sweep's own last
-- success: the tier's in-task gate reads the same monitor.service_audit
-- clock (>= WEEKLY_GATE_DAYS since the last normal exit), so the
-- weekly re-run only actually fires when the sweep is genuinely stale
-- (missed fires all week; the checker applies its max_age_days
-- threshold to age_days). Serves downloads.index.csindex.pe
-- (NonDailyOps, server_cluster/monitor/non_daily.py). Added with the
-- price/PE service split 2026-11-23 (the nightly --pe-only step it
-- replaced never carried its own non-daily check — the weekly PE
-- series' staleness was invisible to the Cautions tab until now; PE
-- publishes with the quotes, so file-mtime freshness of the history
-- CSVs cannot serve here either).
-- args: none
SELECT COUNT(*) AS n_rows,
       COALESCE(EXTRACT(EPOCH FROM (now() - MAX(ended_at)))
                / 86400.0, 1000000000) AS age_days
  FROM monitor.service_audit
 WHERE service = 'downloads.index.csindex.pe'
   AND is_normal_exit
   AND ended_at IS NOT NULL
