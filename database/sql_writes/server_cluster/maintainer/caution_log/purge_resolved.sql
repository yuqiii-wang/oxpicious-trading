-- SQL write action — monitor.caution_log DELETE (retention purge).
-- Executed by server_cluster/maintainer/jobs.py (LogCleanupJob.
-- _purge_resolved_cautions, the shared 03:30 log-cleanup pass): RESOLVED
-- caution rows whose resolved_timestamp is older than the caution
-- retention window go away; OPEN rows are NEVER purged — they are the
-- working set the Cautions tab shows.
--
-- Params (asyncpg $n, in order):
--   $1 retention_days INTEGER — resolve-age cutoff in days (180)
DELETE FROM monitor.caution_log
WHERE is_resolved
  AND resolved_timestamp IS NOT NULL
  AND resolved_timestamp < now() - make_interval(days => $1::integer::int)
