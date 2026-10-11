-- ============================================================================
--  Service heartbeat — current-state liveness rows for EVERY service process.
--
--  One row per service, keyed by its unique service name (the dotted
--  module path). The always-on stream pollers + the watchdog beat as
--  managed services (restart supervision); every OTHER process — batch
--  builds / downloads / analyses / strategy / live / llm runs — registers
--  from DataPipeline.execute() or autoregister() (_common/
--  service_heartbeat.py), so its row shows the LAST RUN (live beat while
--  running; stop_reason / stopped_at between runs). Each registered
--  process runs a daemon thread that upserts its row every 60s regardless
--  of what the main loop is doing. A MISSING or STALE beat_at — never a
--  data gap — is the liveness signal: pollers legitimately sleep between
--  fetch windows, so beat freshness tracks process death/wedging, not
--  market hours. Per-run HISTORY (one row per process life, including the
--  watchdog restart trail) lives in monitor.service_audit; this table is
--  the overwritten CURRENT state.
--
--  Per-service concurrency CONFIG (max_parallel) is static JSON —
--  database/sql/monitoring/static/service_config/<dotted.service.path>.json
--  (tree-root default.json fallback, default cap 1) —
--  read at registration; the live instance counter (live_parallel) lives
--  on THIS overwritten CURRENT-state row (added by
--  10_static_json_config.sql, migrated from the retired
--  monitor.service_config).
--
--  Scope taxonomy: scope is the top-level pipeline area (downloads, builds,
--  analysis, strategy, live, database), sub_scope the level below it (e.g.
--  'stream' under downloads). Both are written by the process itself on
--  every beat so the audit trail is self-contained.
--
--  Stop tracking: stop_reason / is_normal_exit / stopped_at are NULL while
--  the process runs. A clean interpreter exit (Ctrl-C handled by the app,
--  --once completion) records is_normal_exit = true; SIGTERM, an unhandled
--  exception, a watchdog kill, or death WITHOUT any record (SIGKILL / OOM /
--  power loss — detected by the watchdog) records is_normal_exit = false.
--
--  Applied after 00_monitor_schema.sql. Idempotent, including the rename
--  from the pre-2026-10-09 table name stream_heartbeat.
-- ============================================================================

-- Migration: rename monitor.stream_heartbeat → monitor.service_heartbeat
-- (the table long outgrew streams — it carries every batch pipeline too).
-- Must run BEFORE the CREATE below so an existing install's rows land in
-- the renamed table instead of a fresh empty one.
DO $$
BEGIN
    IF to_regclass('monitor.stream_heartbeat') IS NOT NULL
       AND to_regclass('monitor.service_heartbeat') IS NULL THEN
        ALTER TABLE monitor.stream_heartbeat RENAME TO service_heartbeat;
        ALTER TABLE monitor.service_heartbeat
            RENAME CONSTRAINT stream_heartbeat_pkey TO service_heartbeat_pkey;
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS monitor.service_heartbeat (
    service        text        NOT NULL PRIMARY KEY,
    pid            integer     NOT NULL,
    started_at     timestamptz NOT NULL,
    beat_at        timestamptz NOT NULL DEFAULT now(),
    scope          text        NOT NULL DEFAULT 'unknown',
    sub_scope      text,
    stop_reason    text,
    is_normal_exit boolean,
    stopped_at     timestamptz
);

COMMENT ON TABLE monitor.service_heartbeat IS
    'Current-state liveness rows for every registered service process: '
    'the always-on stream pollers + stream_watchdog (restart-supervised) '
    'AND every batch pipeline (last-run state; run history is in '
    'monitor.service_audit). Upserted every ~60s by a daemon thread '
    'inside each process. service is the process unique name (dotted '
    'module path). A stale beat_at means the process is dead or wedged — '
    'poll-loop data gaps are NOT reflected here by design (pollers '
    'legitimately sleep between fetch windows). Written by _common/'
    'service_heartbeat.py; read by database/stream_watchdog.py, '
    'database/monitoring.py --streams and data_viz/api/routes/monitor.ts.';

COMMENT ON COLUMN monitor.service_heartbeat.scope IS
    'Top-level pipeline area: downloads / builds / analysis / strategy / '
    'live / database. Written by the process itself on every beat; '
    '''unknown'' = pre-scope row awaiting its first scoped beat.';

COMMENT ON COLUMN monitor.service_heartbeat.sub_scope IS
    'Level below scope (e.g. ''stream'' under downloads).';

COMMENT ON COLUMN monitor.service_heartbeat.stop_reason IS
    'NULL while running; otherwise why the process stopped (clean exit, '
    'SIGTERM, unhandled exception, watchdog kill, or terminated without '
    'any exit record).';

COMMENT ON COLUMN monitor.service_heartbeat.is_normal_exit IS
    'NULL while running; true = clean interpreter exit, false = signal / '
    'crash / watchdog kill / death without exit record.';

COMMENT ON COLUMN monitor.service_heartbeat.stopped_at IS
    'When the stop was recorded (exit handler, or watchdog detection for '
    'unrecorded deaths).';
