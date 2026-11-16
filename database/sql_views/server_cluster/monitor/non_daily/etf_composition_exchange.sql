-- Non-daily check — ONE exchange's ETF composition snapshots vs the
-- daily ETF identities: every ETF the daily pipeline says exists
-- (latest stats.etf_identity date, the given exchange) that has EVER
-- carried a composition snapshot but whose LATEST snapshot predates the
-- current quarter. Serves downloads.etf.{sse,szse}.composition
-- (NonDailyOps, server_cluster/monitor/non_daily.py).
-- args:
--   exchange: value text — the etf_identity exchange scope ('SS' | 'SZ')
--   grace_days: value int — days after a quarter start before a
--               pre-quarter snapshot counts as missing (the refresh
--               lands on the quarter's first TRADING day, never the 1st)
WITH expected AS (
    SELECT e.code, e.name
      FROM stats.etf_identity e
     WHERE e.date = (SELECT MAX(date) FROM stats.etf_identity)
       AND e.exchange = {{exchange}}::text
       AND EXISTS (SELECT 1
                     FROM stats.sec_composition c
                    WHERE c.source_type = 'etf' AND c.code = e.code)
),
latest AS (
    SELECT code, MAX(snapshot_date) AS snapshot_date
      FROM stats.sec_composition
     WHERE source_type = 'etf'
     GROUP BY code
)
SELECT e.code, e.name, l.snapshot_date AS latest_snapshot_date
  FROM expected e
  LEFT JOIN latest l ON l.code = e.code
 WHERE l.snapshot_date IS NULL
    OR l.snapshot_date < date_trunc('quarter', current_date)
                         - {{grace_days}}::int * interval '1 day'
