-- The latest SUCCESSFUL source-data backup batch — the incremental
-- cutoff of the next mirror pass (its started_at becomes robocopy
-- /MAXAGE's local date). A failed/partial batch is never the cutoff:
-- the next pass falls back to the full mirror and self-heals.
-- args: (none)
-- serves: server_cluster/maintainer/jobs.py StatsDbBackupJob
SELECT id,
       src_root,
       dest_root,
       mode,
       num_files,
       num_skipped,
       num_bytes,
       started_at
FROM maintenance.src_data_backup
WHERE status = {{ok_status}}::smallint
ORDER BY started_at DESC
LIMIT 1
