-- ============================================================================
--  DB per-table workload audit — append-only read/write samples per table,
--  from pg_stat_user_tables cumulative counters.
--
--  ACTIVITY-SPARSE rows by database/db_table_audit.py (always-on,
--  default --interval 300s, heartbeat-registered + watchdog-restarted
--  like database.db_health): each pass snapshots every user table's
--  cumulative counters (seq/idx scans, tuples read/fetched, tuples
--  inserted/updated/deleted, live/dead tuples, total relation size) and
--  writes the DELTA against the previous pass as rates (rows/s) + delta
--  counts — but ONLY for tables that were ACTIVE in the window (any of
--  the seven counters moved) or whose write_flag CHANGED (a clear is
--  always recorded); a baseline pass after a sampler (re)start writes
--  one census row per table (NULL rates + size/live-tup snapshot).
--  With ~1500 user tables a dense per-pass row set would itself be the
--  heaviest write load in the DB — idle tables write nothing, the gap
--  between two checked_at stamps IS the idle signal, and "latest row
--  per table" reads as "last active pass". pg_stat_user_tables counters
--  are cumulative since the last stats reset, so the DIFF between
--  consecutive passes is the window's workload; a counter DECREASE
--  (stats reset, restart) or a new table's first pass means the delta
--  is unknowable — rate columns go NULL for that pass and the next pass
--  re-baselines.
--
--  Both instances are sampled (the db_health contract): the PRIMARY row is
--  the write-side truth (writes go to the primary) plus primary-served
--  reads; the REPLICA row carries the API's read load plus its WAL-replay
--  apply activity. Rows are skipped for an unreachable instance (unlike
--  db_health there is no per-table "unreachable" row to write — the gap
--  itself shows as NULL rates on the next pass).
--
--  write_flag is the write-heavy ALERT verdict, evaluated on PRIMARY rows
--  only: write_rate vs the sampler's fixed amber/red thresholds (module
--  constants, env-overridable). NULL when not evaluated — first/reset
--  pass, or replica rows (replay apply is not "writing", it mirrors it).
--  Flag TRANSITIONS (ok→amber→red→…→ok) are additionally logged as
--  append-only rows in monitor.db_table_alert.
--
--  Type columns are SMALLINT label codes (the label-code contract,
--  static.label_dim + _common/labels.py + data_viz/api/services/infra/
--  label-codes.ts):
--    instance    — domain 'db_instance': 0 primary | 1 replica
--    write_flag  — domain 'db_write_flag': 0 ok | 1 amber | 2 red
--
--  Retention: the sampler purges rows older than --retain-days (default
--  30) each pass — volume is activity-bound, not tables × passes.
--
--  Registered in 00_init.sql after db_health.sql.
-- ============================================================================

CREATE TABLE IF NOT EXISTS monitor.db_table_audit (
    id               bigserial    PRIMARY KEY,
    instance         SMALLINT     NOT NULL,  -- label code (static.label_dim 'db_instance'): 0 primary | 1 replica
    checked_at       timestamptz  NOT NULL DEFAULT now(),
    table_schema     text         NOT NULL,
    table_name       text         NOT NULL,
    window_s         real,                   -- elapsed seconds to the previous pass; NULL when rates are unknown (first/reset pass)
    write_rate       numeric(12,3),          -- (Δins + Δupd + Δdel rows) / window_s — the write-heavy alert metric
    ins_rows         bigint,                 -- Δ n_tup_ins over the window
    upd_rows         bigint,                 -- Δ n_tup_upd
    del_rows         bigint,                 -- Δ n_tup_del
    read_rate        numeric(12,3),          -- (Δ seq_scan + Δ idx_scan) / window_s — scans/s
    seq_scan         bigint,                 -- Δ seq_scan
    idx_scan         bigint,                 -- Δ idx_scan
    seq_tup_read     bigint,                 -- Δ seq_tup_read
    idx_tup_fetch    bigint,                 -- Δ idx_tup_fetch
    n_live_tup       bigint,                 -- snapshot n_live_tup at this pass
    n_dead_tup       bigint,                 -- snapshot n_dead_tup at this pass
    table_size_bytes bigint,                 -- pg_total_relation_size at this pass (heap + indexes + toast)
    write_flag       SMALLINT                -- label code (static.label_dim 'db_write_flag'): 0 ok | 1 amber | 2 red; NULL when not evaluated (first/reset pass, replica rows)
);

CREATE INDEX IF NOT EXISTS db_table_audit_recent_idx
    ON monitor.db_table_audit (instance, checked_at DESC);

CREATE INDEX IF NOT EXISTS db_table_audit_table_recent_idx
    ON monitor.db_table_audit (instance, table_schema, table_name, checked_at DESC);

-- flagged history — the UI's amber/red trail without scanning green rows
CREATE INDEX IF NOT EXISTS db_table_audit_flagged_idx
    ON monitor.db_table_audit (table_schema, table_name, checked_at DESC)
    WHERE write_flag > 0;

COMMENT ON TABLE monitor.db_table_audit IS
    'Append-only per-table workload samples by database/db_table_audit.py '
    '(~300s always-on), ACTIVITY-SPARSE: a row only for tables active in '
    'the window (any counter moved) or whose write_flag changed, plus a '
    'baseline census row per table after a sampler (re)start — read/write '
    'rates and delta counts from pg_stat_user_tables cumulative counters, '
    'plus live/dead tuple and relation-size snapshots. Primary rows are '
    'the write-side truth; replica rows carry API reads + WAL-replay '
    'apply. write_flag (evaluated on primary rows only) is the '
    'fixed-threshold write-heavy verdict; transitions are logged in '
    'monitor.db_table_alert. instance / write_flag are SMALLINT label '
    'codes (static.label_dim domains db_instance / db_write_flag).';

COMMENT ON COLUMN monitor.db_table_audit.instance IS
    'The sampled instance as its static.label_dim (''db_instance'' domain) '
    'SMALLINT code: 0 primary (write-side truth), 1 replica (API reads + '
    'replay apply). Skipped when no replica is configured '
    '(SUPABASE_REPLICA_HOST unset) or the instance is unreachable.';

COMMENT ON COLUMN monitor.db_table_audit.window_s IS
    'Elapsed seconds between this pass and the previous one for the table '
    '(wall clock, not the nominal interval). NULL together with the rate '
    'columns when the delta is unknowable — the first pass after a sampler '
    '(re)start or a stats-counter reset (a counter decrease).';

COMMENT ON COLUMN monitor.db_table_audit.write_rate IS
    '(Δ inserts + Δ updates + Δ deletes) rows per second over the window — '
    'the write-heavy alert metric, judged against the sampler''s fixed '
    'amber/red thresholds. NULL when unknown (see window_s).';

COMMENT ON COLUMN monitor.db_table_audit.read_rate IS
    '(Δ seq_scan + Δ idx_scan) scans per second over the window. '
    'seq_tup_read / idx_tup_fetch carry the row-level read volume. NULL '
    'when unknown (see window_s).';

COMMENT ON COLUMN monitor.db_table_audit.n_live_tup IS
    'Snapshot of pg_stat_user_tables.n_live_tup at this pass — an estimate, '
    'refreshed by ANALYZE / autovacuum, good for bloat trends over passes '
    'not exact row counts.';

COMMENT ON COLUMN monitor.db_table_audit.n_dead_tup IS
    'Snapshot of n_dead_tup at this pass — a sustained climb means updates/'
    'deletes are outpacing vacuum (the audit''s own rows included).';

COMMENT ON COLUMN monitor.db_table_audit.table_size_bytes IS
    'pg_total_relation_size at this pass: heap + indexes + toast. Sampled '
    'per pass, cheap enough at the 300s cadence.';

COMMENT ON COLUMN monitor.db_table_audit.write_flag IS
    'The write-heavy verdict as a static.label_dim (''db_write_flag'' '
    'domain) SMALLINT code: 0 ok | 1 amber | 2 red — write_rate vs the '
    'sampler''s fixed thresholds (amber 100 rows/s, red 1000 rows/s '
    'defaults, env-overridable). Evaluated on PRIMARY rows only; NULL when '
    'not evaluated (first/reset pass, or replica rows — replay apply '
    'mirrors the primary''s writes rather than originating them).';
