-- ============================================================================
--  Service command mailbox — the DOWNWARD channel into a running child
--  (control unification plan Phase 4, 2026-11-21).
--
--  Before this table the only control into a running child was SIGTERM
--  (kill-level). A child polls THIS table from its heartbeat thread
--  (every ~5s — the mailbox's cadence; the 60s beat continues as is)
--  and dispatches cooperative commands at safe checkpoints:
--
--    flush_now (0) — emit/flush the current buffers now (the pollers'
--                    existing "flushed" path)
--    pause     (1) — hold at the next safe checkpoint (bounded: the
--                    child auto-resumes past max_pause_s; gated by the
--                    service_config resource_class — gpu never pauses,
--                    samplers refuse mid-session)
--    resume    (2) — release a held pause
--
--  Exactly-once: ONE conditional UPDATE claims AND consumes the row
--  atomically (the service_heartbeat live_parallel-claim pattern —
--  simultaneous pollers serialize on the row; a claimed row is never
--  delivered twice). Stale unconsumed commands expire (expires_at =
--  created_at + the 120s planned-stop-marker precedent); a consumed
--  row stays for the audit trail and is swept by the janitor.
--
--  Fail-soft contract (the plan's risk register): a DB outage means NO
--  commands flow — work proceeds, the same as beats. The enqueue side
--  is the area servers' POST /processes/{service}/command (gated by
--  the service_config resource_class/pausable flags).
--
--  Applied after service_heartbeat.sql. Idempotent.
-- ============================================================================

CREATE TABLE IF NOT EXISTS monitor.service_command (
    id          bigserial   PRIMARY KEY,
    service     text        NOT NULL,
    command     smallint    NOT NULL,
    payload     jsonb,
    created_at  timestamptz NOT NULL DEFAULT now(),
    expires_at  timestamptz NOT NULL,
    claimed_at  timestamptz,
    claimed_pid integer,
    consumed_at timestamptz
);

CREATE INDEX IF NOT EXISTS idx_service_command_pending
    ON monitor.service_command (service, id)
    WHERE consumed_at IS NULL;

COMMENT ON TABLE monitor.service_command IS
    'The downward command mailbox for running children (Phase 4 of the '
    '2026-11-21 control unification plan): cooperative commands '
    '(flush/pause/resume) enqueued by the area servers'' '
    'POST /processes/{service}/command, claimed AND consumed atomically '
    'by the child''s own heartbeat-thread poll (~5s; SIGTERM→grace→'
    'SIGKILL remains the backstop everywhere). Stale unconsumed rows '
    'expire past expires_at; consumed rows stay as the audit trail. '
    'Written by server_cluster/_common/routers/processes.py (the '
    'enqueue face) and read by _common/service_heartbeat.py (the '
    'claim/dispatch side).';

COMMENT ON COLUMN monitor.service_command.service IS
    'The target child''s heartbeat service name (the dotted module '
    'path) — commands are per service, delivered to the live claimant '
    'of that name.';

COMMENT ON COLUMN monitor.service_command.command IS
    'The command code: smallint label code, domain service_command in '
    'static.label_dim (0 flush_now | 1 pause | 2 resume).';

COMMENT ON COLUMN monitor.service_command.payload IS
    'Optional command arguments (jsonb): e.g. {"max_pause_s": 600} for '
    'a pause (the child''s own config bound still caps it), or the '
    'enqueueing operator''s note.';

COMMENT ON COLUMN monitor.service_command.expires_at IS
    'When an UNCONSUMED row stops being deliverable (created_at + 120s '
    'default — the planned-stop marker''s stale-marker precedent): a '
    'command the child never polled dies here instead of firing late.';

COMMENT ON COLUMN monitor.service_command.claimed_at IS
    'When the child consumed the row (= consumed_at; both kept for the '
    'audit shape). claimed_pid records the consuming process.';

COMMENT ON COLUMN monitor.service_command.consumed_at IS
    'The exactly-once marker: set BY THE CLAIM UPDATE itself, so a '
    'claimed row can never be delivered twice (NULL = still pending, '
    'until expiry).';
