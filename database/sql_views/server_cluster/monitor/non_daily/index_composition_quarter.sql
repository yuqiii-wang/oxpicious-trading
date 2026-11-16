-- Non-daily check — INDEX composition snapshots vs the daily index
-- identities: every index the daily pipeline says exists (latest
-- stats.index_identity date) that has EVER carried a composition
-- snapshot but whose LATEST snapshot predates the current quarter.
-- Serves the shared check of downloads.index.{csindex,szse,bse}.
--composition (NonDailyOps, server_cluster/monitor/non_daily.py).
-- args:
--   grace_days: value int — days after a quarter start before a
--               pre-quarter snapshot counts as missing (the refresh
--               lands on the quarter's first TRADING day, never the 1st)
WITH expected AS (
    SELECT i.code, i.name
      FROM stats.index_identity i
     WHERE i.date = (SELECT MAX(date) FROM stats.index_identity)
       AND i.code NOT LIKE 'DUMMY%'
       -- only indices the composition family has EVER covered: a
       -- brand-new index is the quarter-start refresh's charge, not a
       -- backfill gap (it would otherwise re-run weekly forever)
       AND EXISTS (SELECT 1
                     FROM stats.sec_composition c
                    WHERE c.source_type = 'index' AND c.code = i.code)
),
latest AS (
    SELECT code, MAX(snapshot_date) AS snapshot_date
      FROM stats.sec_composition
     WHERE source_type = 'index'
     GROUP BY code
)
SELECT e.code, e.name, l.snapshot_date AS latest_snapshot_date
  FROM expected e
  LEFT JOIN latest l ON l.code = e.code
 WHERE l.snapshot_date IS NULL
    OR l.snapshot_date < date_trunc('quarter', current_date)
                         - {{grace_days}}::int * interval '1 day'
