-- ============================================================================
--  DB per-table workload audit ARCHIVE — one record per (day, table,
--  instance): a day of monitor.db_table_audit pass-rows collapsed into a
--  single rollup.
--
--  COMPUTED TABLE — monitor.db_table_audit is the source of truth; this
--  table is aggregated from it by database/db_table_audit_archive.py
--  (never hand-written, never read as fresh data). The daily rebuild is
--  IDEMPOTENT per GMT date (the house "operational day" — UTC day of
--  checked_at, i.e. the Beijing 08:00 boundary): a date is stale when the
--  audit gained rows after that date's last computed_at; a stale date's
--  archive rows are purged + re-aggregated in one transaction, so the
--  current partial day refreshes on every run and converges once the day
--  closes, and missed days self-heal.
--
--  Written daily by the maintainer's 03:30 Asia/Shanghai pass
--  (server_cluster/maintainer/jobs.py DbTableAuditArchiveJob, on the
--  MaintenanceScheduler before the 07:00 fleet bring-up and the day's
--  pipelines) and on demand via python -m database.db_table_audit_archive
--  or the maintainer's POST /jobs/db_table_audit_archive/run.
--
--  NO retention purge — this table IS the retention: the audit keeps ~30
--  days of per-pass detail (the sampler's --retain-days); the archive
--  keeps the per-table daily history indefinitely (bounded by active
--  tables x days).
--
--  Type columns are SMALLINT label codes (the label-code contract,
--  static.label_dim + _common/labels.py + data_viz/api/services/infra/
--  label-codes.ts):
--    instance       — domain 'db_instance': 0 primary | 1 replica
--    max_write_flag — domain 'db_write_flag' (reuse): the WORST flag of
--                     the day, 0 ok | 1 amber | 2 red; NULL when no pass
--                     of the day was evaluated (baseline census only)
--
--  Registered in 00_init.sql after db_table_audit.sql.
-- ============================================================================

CREATE TABLE IF NOT EXISTS monitor.db_table_audit_archive (
    date                 date         NOT NULL,   -- GMT operational day (UTC day of checked_at — Beijing 08:00 boundary)
    instance             SMALLINT     NOT NULL,   -- label code (static.label_dim 'db_instance'): 0 primary | 1 replica
    table_schema         text         NOT NULL,
    table_name           text         NOT NULL,
    active_passes        integer      NOT NULL,   -- audit rows collapsed into this record
    ins_rows             bigint       NOT NULL DEFAULT 0,  -- day sum of insert-row deltas
    upd_rows             bigint       NOT NULL DEFAULT 0,  -- day sum of update-row deltas
    del_rows             bigint       NOT NULL DEFAULT 0,  -- day sum of delete-row deltas
    write_rows_total     bigint      GENERATED ALWAYS AS (ins_rows + upd_rows + del_rows) STORED,
    peak_write_rate      numeric(12,3),           -- max per-pass write_rate of the day (rows/s)
    peak_read_rate       numeric(12,3),           -- max per-pass read_rate of the day (scans/s)
    seq_scan             bigint       NOT NULL DEFAULT 0,  -- day sum
    idx_scan             bigint       NOT NULL DEFAULT 0,  -- day sum
    seq_tup_read         bigint       NOT NULL DEFAULT 0,  -- day sum
    idx_tup_fetch        bigint       NOT NULL DEFAULT 0,  -- day sum
    max_write_flag       SMALLINT,               -- worst flag of the day (label 'db_write_flag'): NULL = no evaluated pass
    flagged_passes       integer      NOT NULL DEFAULT 0,  -- passes with write_flag > 0
    end_n_live_tup       bigint,                 -- the day's LAST pass snapshot
    end_n_dead_tup       bigint,
    end_table_size_bytes bigint,
    first_active_at      timestamptz  NOT NULL,   -- the day's first active pass
    last_active_at       timestamptz  NOT NULL,   -- the day's last active pass
    computed_at          timestamptz  NOT NULL DEFAULT now(),  -- staleness watermark (audit rows newer => date rebuilds)
    PRIMARY KEY (date, instance, table_schema, table_name)
);

CREATE INDEX IF NOT EXISTS db_table_audit_archive_table_history_idx
    ON monitor.db_table_audit_archive (table_schema, table_name, date DESC);

COMMENT ON TABLE monitor.db_table_audit_archive IS
    'COMPUTED daily rollup of monitor.db_table_audit: one record per (GMT '
    'day, table, instance) — day sums of row deltas and scan deltas, peak '
    'write/read rates, worst write_flag, end-of-day size/live-tup '
    'snapshots. Rebuilt idempotently per date by database/'
    'db_table_audit_archive.py (stale-date watermark), daily on the '
    'maintainer 03:30 pass. Never hand-written; no retention purge — this '
    'table IS the retention (the audit keeps ~30d of per-pass detail).';

COMMENT ON COLUMN monitor.db_table_audit_archive.date IS
    'The GMT operational day (UTC calendar day of checked_at — the '
    'Beijing 08:00 boundary, the house llm-audit day convention). A pass '
    'at 2026-11-14 17:00 UTC (= Beijing 15th 01:00) belongs to the 14th.';

COMMENT ON COLUMN monitor.db_table_audit_archive.active_passes IS
    'How many activity-sparse audit rows the day collapsed into this one '
    'record — a table active in every 300s pass of a day lands at ~288; 1 '
    'means a single burst window.';

COMMENT ON COLUMN monitor.db_table_audit_archive.write_rows_total IS
    'ins_rows + upd_rows + del_rows (GENERATED). The day''s total rows '
    'written — the long-term trend line the 30-day detail cannot keep.';

COMMENT ON COLUMN monitor.db_table_audit_archive.max_write_flag IS
    'The WORST write-heavy verdict of the day (static.label_dim '
    '''db_write_flag'': 0 ok | 1 amber | 2 red) — a table flagged red in '
    'any pass of the day keeps max_write_flag = 2 here. NULL when no pass '
    'of the day was evaluated (baseline census rows only). '
    'flagged_passes counts the amber/red passes.';

COMMENT ON COLUMN monitor.db_table_audit_archive.end_table_size_bytes IS
    'pg_total_relation_size at the day''s LAST active pass — the '
    'end-of-day size snapshot for long-term growth trending.';

COMMENT ON COLUMN monitor.db_table_audit_archive.computed_at IS
    'When this date was last re-aggregated. A date rebuilds whenever '
    'monitor.db_table_audit gains rows for it newer than this watermark '
    '(the stale-date detection in database/db_table_audit_archive.py).';
