-- SQL write action — maintenance.src_data_backup INSERT (one backup batch).
-- Executed by server_cluster/maintainer/backup.py (BackupAudit.insert,
-- the StatsDbBackupJob audit trail: one row per robocopy mirror pass).
--
-- Params (asyncpg $n, in order):
--   $1 src_root     TEXT      — mirrored tree, Windows form (E:\oxpicious-trading\temps)
--   $2 dest_root    TEXT      — backup drive mirror root, Windows form (D:\oxpicious-stats-db-backup)
--   $3 mode         SMALLINT  — label code domain 'backup_mode': 0 full | 1 incremental
--   $4 status       SMALLINT  — label code domain 'backup_status': 0 ok | 1 partial | 2 failed
--   $5 num_files    INTEGER   — files copied this batch (robocopy Copied)
--   $6 num_skipped  INTEGER   — examined, already up-to-date (robocopy Skipped)
--   $7 num_bytes    BIGINT    — bytes copied this batch (robocopy /BYTES)
--   $8 detail       TEXT      — NULL when clean; else the one-line counts note / failure reason
--   $9 started_at   TIMESTAMPTZ — when the batch started
--   $10 duration_ms INTEGER   — wall time of the robocopy pass
INSERT INTO maintenance.src_data_backup
    (src_root, dest_root, mode, status, num_files, num_skipped,
     num_bytes, detail, started_at, duration_ms)
VALUES ($1::text, $2::text, $3::smallint, $4::smallint, $5::integer,
        $6::integer, $7::bigint, $8::text, $9::timestamptz, $10::integer)
