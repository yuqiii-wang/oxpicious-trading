-- Non-daily check — classification coverage vs the daily stock
-- identities: stocks the daily pipeline says exist (latest
-- stats.stock_identity date) with no stats.sec_classification row.
-- Serves builds.classification (the once-build whose output should
-- cover the whole universe; new listings appear here until the
-- Saturday backfill re-runs it) (NonDailyOps,
-- server_cluster/monitor/non_daily.py).
-- args: none
WITH expected AS (
    SELECT s.code, s.name
      FROM stats.stock_identity s
     WHERE s.date = (SELECT MAX(date) FROM stats.stock_identity)
)
SELECT e.code, e.name
  FROM expected e
  LEFT JOIN stats.sec_classification c ON c.code = e.code
 WHERE c.code IS NULL
