-- ============================================================================
--  DB health samples — append-only time series for the primary/replica
--  Postgres pair (docker-compose: oxpicious-db :9876 + oxpicious-db-replica
--  :9877).
--
--  One row per INSTANCE per sample pass: was it reachable, how fast, what
--  are its DB-internal vitals (uptime, connections, size, cache hit,
--  commit count, WAL shipping state + replay backlog) and its container's
--  CPU/memory (docker stats). database/db_health.py samples every ~60s
--  (always-on, heartbeat-registered, restarted by the stream watchdog like
--  the downloads pollers); database/monitoring.py stays the alert-only
--  point-in-time checker — this table is the HISTORY those checks can be
--  reconstructed from ("when did the replica last stream? how hot did the
--  primary run during the bulk load?").
--
--  A sample of an UNREACHABLE instance is still recorded (healthy=false +
--  a short error, vitals NULL, container stats filled when Docker still
--  reports the container): outages leave a trail, not a gap. Rows live
--  in the DB being sampled — while the primary itself is down the sampler
--  can only log [ALERT] lines to logs/<date>/db_health.log; sampling
--  resumes when it recovers.
--
--  Type columns are SMALLINT label codes (the label-code contract,
--  static.label_dim + _common/labels.py + data_viz/api/services/
--  label-codes.ts):
--    instance          — domain 'db_instance': 0 primary | 1 replica
--    replication_state — domain 'db_replication_state': the WAL shipping
--                        state AS SEEN FROM THE SAMPLED INSTANCE (primary:
--                        the standby's pg_stat_replication.state; replica:
--                        pg_stat_wal_receiver.status); 0 streaming |
--                        1 catchup | 2 startup | 3 backup | 4 stopping |
--                        5 connecting | 6 stopped | 99 none
--
--  Retention: the sampler purges rows older than --retain-days (90) each
--  pass (~2 rows/min — small, but bounded on principle).
--
--  Applied after 05_external_resource_audit.sql.
-- ============================================================================

CREATE TABLE IF NOT EXISTS monitor.db_health (
    id                   bigserial    PRIMARY KEY,
    instance             SMALLINT     NOT NULL,  -- label code (static.label_dim 'db_instance'): 0 primary | 1 replica
    checked_at           timestamptz  NOT NULL DEFAULT now(),
    healthy              boolean      NOT NULL,  -- reachable AND WAL shipping state = streaming
    connect_ms           integer,                -- connect + vitals query round-trip; NULL when unreachable
    error                text,                   -- NULL when healthy; on failure a short reason (exception class + message)
    uptime_s             bigint,                 -- seconds since postmaster start; NULL when unreachable
    active_connections   integer,                -- pg_stat_database.numbackends for this database
    db_size_bytes        bigint,                 -- pg_database_size(current_database())
    cache_hit_ratio      numeric(7,6),           -- pg_stat_database blks_hit/(blks_hit+blks_read), cumulative since start
    xact_commit          bigint,                 -- pg_stat_database.xact_commit, cumulative (rate = diff between samples)
    replication_state    SMALLINT,               -- label code (static.label_dim 'db_replication_state'), instance-relative
    replay_backlog_bytes bigint,                 -- primary: standby replay backlog (pg_wal_lsn_diff to replay_lsn); replica: pg_last_wal_receive_lsn vs pg_last_wal_replay_lsn gap
    wal_dir_bytes        bigint,                 -- total size of THIS instance's pg_wal directory (pg_ls_waldir sum); NULL when unreachable
    cpu_pct              numeric(6,2),           -- docker stats CPUPerc of the instance's container; NULL when Docker is unavailable
    mem_used_bytes       bigint,                 -- docker stats MemUsage, used part
    mem_limit_bytes      bigint,                 -- docker stats MemUsage, limit part
    mem_pct              numeric(5,2)            -- docker stats MemPerc
);

CREATE INDEX IF NOT EXISTS db_health_recent_idx
    ON monitor.db_health (checked_at DESC);

CREATE INDEX IF NOT EXISTS db_health_instance_recent_idx
    ON monitor.db_health (instance, checked_at DESC);

COMMENT ON TABLE monitor.db_health IS
    'Append-only health samples for the primary/replica Postgres pair: one '
    'row per instance per pass by database/db_health.py (~60s always-on) — '
    'reachability + latency, DB-internal vitals (uptime, connections, size, '
    'cache hit, xact_commit, WAL shipping state + replay backlog) and '
    'container CPU/memory from docker stats. Unreachable instances are '
    'sampled too (healthy=false + error) so outages leave a trail. '
    'instance / replication_state are SMALLINT label codes (static.label_dim '
    'domains db_instance / db_replication_state).';

COMMENT ON COLUMN monitor.db_health.instance IS
    'The sampled instance as its static.label_dim (''db_instance'' domain) '
    'SMALLINT code: 0 primary (oxpicious-db :9876), 1 replica '
    '(oxpicious-db-replica :9877). Skipped when no replica is configured '
    '(SUPABASE_REPLICA_HOST unset).';

COMMENT ON COLUMN monitor.db_health.healthy IS
    'TRUE when the instance answered the vitals query AND its WAL shipping '
    'state is streaming. FALSE covers connect/query failures (error set) '
    'and any other shipping state (catchup / none / stopped ...). Replay '
    'backlog does NOT flip it — a burst backlog is the designed catch-up '
    'behavior (see database/REPLICA_SYNC.md), the bytes column carries it.';

COMMENT ON COLUMN monitor.db_health.connect_ms IS
    'Milliseconds for the connect + vitals round-trip, measured client-side '
    'by the sampler. NULL when the instance was unreachable.';

COMMENT ON COLUMN monitor.db_health.error IS
    'NULL when healthy (by contract — no success text is ever written). On '
    'failure a SHORT reason: exception class + message, truncated at write '
    'time.';

COMMENT ON COLUMN monitor.db_health.uptime_s IS
    'Seconds since the instance''s postmaster start — a restart (crash, '
    'OOM, host reboot) shows as an uptime reset. NULL when unreachable.';

COMMENT ON COLUMN monitor.db_health.cache_hit_ratio IS
    'pg_stat_database blks_hit / (blks_hit + blks_read), CUMULATIVE since '
    'postmaster start — a slow-moving health indicator; per-window ratios '
    'come from diffing consecutive samples.';

COMMENT ON COLUMN monitor.db_health.xact_commit IS
    'pg_stat_database.xact_commit, cumulative since postmaster start — '
    'commit rate = diff between consecutive samples.';

COMMENT ON COLUMN monitor.db_health.replication_state IS
    'The WAL shipping state AS SEEN FROM THE SAMPLED INSTANCE, as a '
    'static.label_dim (''db_replication_state'' domain) SMALLINT code: on '
    'the primary the standby''s pg_stat_replication.state, on the replica '
    'pg_stat_wal_receiver.status. 99 none = no standby attached (primary) '
    'or no WAL receiver (replica) — both alert-worthy. NULL when '
    'unreachable.';

COMMENT ON COLUMN monitor.db_health.replay_backlog_bytes IS
    'Instance-relative replay backlog in bytes: on the primary, '
    'pg_wal_lsn_diff(pg_current_wal_lsn(), replay_lsn) of the standby '
    '(the same measure the API failover probe and database/monitoring.py '
    'alert on); on the replica, pg_last_wal_receive_lsn vs '
    'pg_last_wal_replay_lsn. NULL when unavailable (no standby / nothing '
    'received yet / unreachable).';

COMMENT ON COLUMN monitor.db_health.cpu_pct IS
    'The instance container''s CPU percentage from `docker stats '
    '--no-stream` (one call per pass covers both containers). NULL when '
    'Docker is unavailable or the container is gone.';

COMMENT ON COLUMN monitor.db_health.mem_used_bytes IS
    'The instance container''s used memory in bytes from `docker stats '
    '--no-stream` MemUsage. NULL when Docker is unavailable.';

-- 2026-10 WAL disk: the sampler already recorded the SLOT-retained WAL via
-- the replication legs; this is the pg_wal DIRECTORY size of each instance
-- (recyclable segments included). Steady state it idles under max_wal_size
-- (recycled-for-reuse); growth beyond ~1.5× max_wal_size means something is
-- PINNING segments — the down/inactive slot case database/monitoring.py
-- alerts on, or checkpoint/replay trouble. database/monitoring.py
-- check_health alerts on the primary's copy of this number per pass.
ALTER TABLE monitor.db_health ADD COLUMN IF NOT EXISTS wal_dir_bytes bigint;

COMMENT ON COLUMN monitor.db_health.wal_dir_bytes IS
    'Total size of THIS instance''s pg_wal directory (sum of pg_ls_waldir): '
    'recyclable segments included, so it legitimately idles under '
    'max_wal_size. Sustained growth beyond ~1.5× max_wal_size = segments '
    'are being pinned (down slot, stuck replay/checkpoint). NULL when the '
    'instance was unreachable.';
