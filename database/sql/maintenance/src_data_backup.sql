-- ============================================================================
--  Source-data backup audit — append-only, ONE ROW PER BACKUP BATCH.
--
--  The audit trail of the maintainer's stats-db backup job
--  (server_cluster/maintainer/jobs.py StatsDbBackupJob): each daily
--  mirror pass of the repo's temps/ source-data tree onto the secondary
--  backup drive inserts one row — how many files it actually copied
--  (and how many it examined and found already up-to-date), the byte
--  volume, when it ran and how long it took, and whether it completed.
--
--  The copy itself is a robocopy mirror pass (disk-speed /MT parallel
--  copy — no per-file throttling): with no prior batch it mirrors the
--  whole tree (mode full); with a prior OK batch it hands robocopy
--  /MAXAGE = that batch's local date, so only files newer than the last
--  backup are candidates (mode incremental) — robocopy then still
--  compares each candidate against the destination mirror, so a file is
--  copied only when it is genuinely missing or changed there.
--
--  The latest OK batch's started_at IS the incremental cutoff of the
--  next pass (self-healing: a failed or never-run backup falls back to
--  the full mirror). Append-only, one thin row per day — no retention
--  purge.
--
--  Type columns are SMALLINT label codes (the label-code contract,
--  static.label_dim + _common/labels.py + data_viz/api/services/
--  infra/label-codes.ts):
--    mode   — domain 'backup_mode':   0 full | 1 incremental
--    status — domain 'backup_status': 0 ok | 1 partial | 2 failed
--  src_root / dest_root stay TEXT on purpose: drive paths are an open
--  vocabulary (the same deliberate exception as
--  monitor.service_audit.service).
--
--  Applied after 00_maintenance_schema.sql.
--
-- description: Per-batch audit of the source-data (temps) mirror onto the secondary backup drive — how many files each backup copied, when it ran, and whether it completed.
-- tags: backup, 备份, source data, 源数据, mirror, 镜像, audit, 审计, maintenance, 维护, stats-db backup
-- ============================================================================

CREATE TABLE IF NOT EXISTS maintenance.src_data_backup (
    id             bigserial    PRIMARY KEY,
    src_root       text         NOT NULL,  -- mirrored tree, Windows form (robocopy native): E:\oxpicious-trading\temps
    dest_root      text         NOT NULL,  -- backup drive mirror root, Windows form: D:\oxpicious-stats-db-backup
    mode           SMALLINT     NOT NULL,  -- label code (static.label_dim 'backup_mode'): 0=full 1=incremental
    status         SMALLINT     NOT NULL,  -- label code (static.label_dim 'backup_status'): 0=ok 1=partial 2=failed
    num_files      INTEGER      NOT NULL DEFAULT 0,  -- files copied this batch (robocopy summary "Copied")
    num_skipped    INTEGER      NOT NULL DEFAULT 0,  -- examined, already up-to-date at dest (robocopy "Skipped")
    num_bytes      BIGINT       NOT NULL DEFAULT 0,  -- bytes copied this batch (robocopy /BYTES summary)
    detail         text,                   -- NULL = clean; else one line: robocopy counts note / failure reason
    started_at     timestamptz  NOT NULL,
    duration_ms    INTEGER      NOT NULL,
    created_at     timestamptz  NOT NULL DEFAULT now()
);

-- The cutoff read (latest OK batch) and any UI's recent-batches scan hit
-- started_at DESC.
CREATE INDEX IF NOT EXISTS src_data_backup_started_idx
    ON maintenance.src_data_backup (started_at DESC);

COMMENT ON TABLE  maintenance.src_data_backup        IS 'Append-only per-batch audit of the source-data mirror (temps/ -> the stats-db backup drive): one row per robocopy batch with files/bytes copied, up-to-date skips, run time and outcome. Written by the server-cluster maintainer''s daily backup job; the latest OK batch is the next pass''s incremental cutoff.';
COMMENT ON COLUMN maintenance.src_data_backup.src_root    IS 'The mirrored source tree, Windows form (robocopy runs as a Windows process via WSL interop): E:\oxpicious-trading\temps.';
COMMENT ON COLUMN maintenance.src_data_backup.dest_root   IS 'The backup-drive mirror root, Windows form: D:\oxpicious-stats-db-backup (a different physical volume — the copy must not live under the project dir).';
COMMENT ON COLUMN maintenance.src_data_backup.mode        IS 'Batch mode — SMALLINT label code, domain ''backup_mode'' (static.label_dim): 0 = full (no prior OK batch: mirror the whole tree), 1 = incremental (robocopy /MAXAGE = the latest OK batch''s local date, only newer files are candidates).';
COMMENT ON COLUMN maintenance.src_data_backup.status      IS 'Batch outcome — SMALLINT label code, domain ''backup_status'' (static.label_dim): 0 = ok (robocopy exit 0-3), 1 = partial (exit 4-7: mismatches present), 2 = failed (exit 8+: copy failures / fatal error).';
COMMENT ON COLUMN maintenance.src_data_backup.num_files   IS 'Files actually copied this batch (robocopy summary Copied). 0 with num_skipped > 0 = nothing changed since the last batch — the healthy daily shape.';
COMMENT ON COLUMN maintenance.src_data_backup.num_skipped IS 'Files examined and found already up-to-date at the destination (robocopy summary Skipped) — the mirror''s coverage evidence.';
COMMENT ON COLUMN maintenance.src_data_backup.num_bytes   IS 'Bytes copied this batch (robocopy /BYTES summary, exact).';
COMMENT ON COLUMN maintenance.src_data_backup.detail      IS 'NULL = clean pass; else one human line — the robocopy count note (mismatch / failure counts) or the failure reason (timeout, drive not mounted, spawn error).';
COMMENT ON COLUMN maintenance.src_data_backup.started_at  IS 'When the batch started (UTC) — the NEXT batch''s incremental cutoff comes from the latest OK row''s value converted to local date.';
COMMENT ON COLUMN maintenance.src_data_backup.duration_ms IS 'Wall time of the robocopy pass in ms (the batch = the mirror run, not the audit write).';
