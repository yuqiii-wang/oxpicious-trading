-- ============================================================================
--  Orchestration task state — the ARRANGEMENT of ONE orchestration-tier
--  service (one row per service across the scripts/orchestration/*.json
--  tier files): whether the maintainer-driven pass should RUN it (due)
--  or SKIP it (not_due), computed from the service_config static tree's
--  ``freq`` window vs the last successful exit in monitor.service_audit.
--
--  The freq-to-run already lives in service_config (database/sql/
--  monitoring/static/service_config/<dotted.service>.json); this table is
--  the DURABLE record of the arrangement the monitor area's
--  OrchestrationArranger (server_cluster/monitor/arrangement.py) derives
--  from it — the maintainer's nightly chain (server_cluster/maintainer/
--  pipeline.py) requests ``arrange: true`` per tier, so the orchestration
--  runner SKIPS not-due steps (state skipped_not_due) instead of
--  spawning them to internally no-op off-cadence, and the Monitor UI's
--  Orchestration page (GET /api/monitor/orchestration/board) shows the
--  three sections: live running (heartbeat-joined), the to-run queue,
--  and the recently-no-need rows.
--
--  The arrangement rule is the SAME one the UI run guard applies
--  (FREQ_PERIOD_MS / SKIP_ANY_SUCCESS in data_viz/api/routes/monitor/
--  _shared.ts): a service is not_due iff its freq window contains the
--  last successful exit (or freq "once" with any success ever). A
--  service with NO own config file (the default.json fallback) is always
--  arranged due — an unconfigured tier step keeps today's run-every-pass
--  behavior; ``force`` on the run request bypasses skips for manual
--  re-runs. RESIDENTS (service_config is_resident — always live, no
--  freq) are never arranged: the arranger skips them, so they hold no
--  row here (a resident riding a tier step still spawns every pass —
--  it just dedupes onto the live process via the max_parallel claim).
--
--  state is a SMALLINT label code (the label-code contract, static.
--  label_dim + _common/labels.py + data_viz/api/services/infra/
--  label-codes.ts):
--    orchestration_plan_state — 0 unknown (never arranged) | 1 due |
--                               2 not_due
--
--  The per-run trail is NOT here: every spawned step is a real
--  run-registry child whose monitor.service_audit row already records
--  started/ended/exit. This table is the CURRENT arrangement (upsert,
--  not append); due_at is the freq window's re-open time (NULL = no
--  window: never succeeded, or a no-period freq — occasionally /
--  on_demand — or once).
--
--  Applied via monitoring/00_init.sql.
-- ============================================================================

CREATE TABLE IF NOT EXISTS monitor.orchestration_task_state (
    service         TEXT        NOT NULL,
    tier            TEXT        NOT NULL,
    step            TEXT        NOT NULL DEFAULT '',
    freq            TEXT        NOT NULL,
    priority        INTEGER     NOT NULL DEFAULT 0,
    state           SMALLINT    NOT NULL DEFAULT 0,  -- label code (static.label_dim 'orchestration_plan_state'): 0=unknown 1=due 2=not_due
    last_success_at TIMESTAMPTZ,                     -- the arrangement's input snapshot (max normal-exit ended_at)
    due_at          TIMESTAMPTZ,                     -- freq window re-open (last_success_at + period); NULL = every pass / once / no window
    arranged_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT pk_orchestration_task_state PRIMARY KEY (service)
);

COMMENT ON TABLE  monitor.orchestration_task_state                     IS 'Per-orchestration-service arrangement state: the maintainer-driven run/don''t-run decision (due / not_due) computed by the monitor area''s OrchestrationArranger from the service_config freq window vs the last successful exit. The nightly chain arranges each tier before running it (not-due steps are skipped, not spawned); the Monitor UI''s Orchestration page shows the arrangement (running / queue / no-need).';
COMMENT ON COLUMN monitor.orchestration_task_state.service             IS 'The tier step''s dotted python -m module path — the key the service_config tree and the heartbeat/audit tables hang off. One row per service (first tier occurrence when a service appears in more than one tier file).';
COMMENT ON COLUMN monitor.orchestration_task_state.tier                IS 'The orchestration tier file stem the step belongs to (downloads_daily, analysis_weekly, ...).';
COMMENT ON COLUMN monitor.orchestration_task_state.step                IS 'The step''s numeric key within its tier file (display order context for the board).';
COMMENT ON COLUMN monitor.orchestration_task_state.freq                IS 'The effective freq from the service_config static tree (the value the arrangement was computed with — the own file''s, else the default.json fallback''s).';
COMMENT ON COLUMN monitor.orchestration_task_state.priority            IS 'The service''s service_config priority (the orchestration give-way rank; the queue section sorts by it — downloads outrank non-downloads, then frequency).';
COMMENT ON COLUMN monitor.orchestration_task_state.state               IS 'Arrangement verdict — SMALLINT label code, domain ''orchestration_plan_state'' (static.label_dim): 0 = unknown (never arranged), 1 = due (run it in the arranged pass), 2 = not_due (the freq window holds, or once + a success ever — the pass skips the step).';
COMMENT ON COLUMN monitor.orchestration_task_state.last_success_at     IS 'The arrangement''s input snapshot: the service''s last normal-exit ended_at from monitor.service_audit at arrange time.';
COMMENT ON COLUMN monitor.orchestration_task_state.due_at              IS 'When the freq window re-opens (last_success_at + period). NULL = no window to wait out: never succeeded (due now), or a no-period freq (occasionally / on_demand), or once (satisfied by any success — the state column carries the verdict). Residents (is_resident) hold no row here at all.';
COMMENT ON COLUMN monitor.orchestration_task_state.arranged_at         IS 'When the arranger wrote this verdict (the board''s success-since-arranged demotion keys off it).';

-- The board's queue scan: due rows awaiting their pass.
CREATE INDEX IF NOT EXISTS idx_orchestration_task_state_due
    ON monitor.orchestration_task_state (priority DESC, service)
    WHERE state = 1;
