-- ============================================================================
--  Service audit — append-only history of service process runs.
--
--  One row per REGISTERED PROCESS LIFE (not per service): _common/
--  service_heartbeat.py inserts a row when a process registers (in
--  register_service — called for EVERY pipeline process via
--  DataPipeline.execute() / autoregister() / the explicit stream calls)
--  and closes it on exit (ended_at / exit_reason / is_normal_exit — same
--  vocabulary as service_heartbeat's stop columns).
--  monitor.service_heartbeat stays the CURRENT-state table (one row per
--  service, overwritten); this table is the per-run history.
--
--  Watchdog restart trail (merged from monitor.stream_restart, 2026-10-09):
--  a restart ends one life and starts the next, so the restart reason is
--  recorded on the DEAD life's row — database/stream_watchdog.py closes it
--  with a 'watchdog restart: …' exit_reason. The restart reads (24h
--  counts, the UI's restart trail) filter on that prefix via the partial
--  index below.
--
--  A process that dies WITHOUT an exit record (SIGKILL / OOM / power loss)
--  leaves ended_at NULL; database/stream_watchdog.py closes such rows when
--  it detects the death (stream services on restart, every other process
--  in its per-cycle orphan pass; ended_at estimated from the heartbeat's
--  last beat_at). Rows staying open therefore mean "no watchdog was
--  running" — or the process is genuinely still alive (a registration
--  also counts open rows with live pids toward the heartbeat row's
--  max_parallel cap).
--
--  Applied after 01_service_heartbeat.sql.
-- ============================================================================

CREATE TABLE IF NOT EXISTS monitor.service_audit (
    id             bigserial    PRIMARY KEY,
    service        text         NOT NULL,
    scope          text         NOT NULL DEFAULT 'unknown',
    sub_scope      text,
    pid            integer      NOT NULL,
    started_at     timestamptz  NOT NULL DEFAULT now(),
    ended_at       timestamptz,
    exit_reason    text,
    is_normal_exit boolean
);

CREATE INDEX IF NOT EXISTS service_audit_service_recent_idx
    ON monitor.service_audit (service, started_at DESC);

-- Partial index for the "still open" lookup the watchdog reconciles.
CREATE INDEX IF NOT EXISTS service_audit_open_idx
    ON monitor.service_audit (service)
    WHERE ended_at IS NULL;

-- Partial index for the merged watchdog restart trail: 24h restart counts
-- (database/monitoring.py) and the UI's recent-restarts list filter on the
-- 'watchdog restart:' exit_reason prefix.
CREATE INDEX IF NOT EXISTS service_audit_watchdog_restart_idx
    ON monitor.service_audit (ended_at DESC)
    WHERE exit_reason LIKE 'watchdog restart%';

COMMENT ON TABLE monitor.service_audit IS
    'Append-only per-run history of registered service processes: one row '
    'per process life (inserted by _common/service_heartbeat.py '
    'register_service, closed on exit or by the stream watchdog for '
    'unrecorded deaths). exit_reason/is_normal_exit use the same vocabulary '
    'as monitor.service_heartbeat''s stop columns; NULL ended_at = the run '
    'never recorded an exit (running, or died without a supervisor to '
    'close it). Watchdog restarts are merged here: a restart closes the '
    'dead life with a ''watchdog restart: …'' exit_reason. Read by '
    'data_viz/api/routes/monitor.ts (recent runs + restart trail).';
