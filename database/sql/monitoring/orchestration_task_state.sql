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
--  THE DATA PRE-CHECK (the 16:00 chain's run / don't-run gate, 2026-11-
--  24): a service that declares one in the static tree
--  database/sql/monitoring/static/orchestration_pre_checks/<dotted.
--  service>.json is arranged by its DATA instead of the calendar — the
--  arranger probes the latest closed sessions and the verdict is what
--  it found (the ``basis`` column):
--   - fetch (a download): the service's data table lacks a session → is
--     its source csv staged? not staged (csv_missing) → RUN the
--     download; staged (csv_present) → skip, the build loads it;
--   - load (a build reading that csv): the data table lacks the session
--     and the csv is staged (csv_present) → RUN the build; not staged
--     (csv_missing) → skip until its download runs;
--   - derive (analysis / strategy / a DB-only build): the result
--     table's pk lacks a session (db_missing) → RUN.
--   Every expected session present (data_present) → skip. A service
--  without a declared pre-check keeps the freq-calendar rule (basis
--  calendar); a probe that errors fails OPEN (check_failed — the step
--  runs, its own guards decide). The chain arranges each tier right
--  before running it, so a tier's verdict sees the tiers before it
--  (the builds' csv_present is the downloads tier's fresh output).
--  The probed table is the DATA table each pipeline gates on, never a
--  stats.*_identity table: the stream pollers upsert the session's
--  identity rows intraday, hours before the EOD build loads anything.
--  Presence is not completeness — a partial session reads present;
--  partial sessions are the morning audit's domain.
--
--  state is a SMALLINT label code (the label-code contract, static.
--  label_dim + _common/labels.py + data_viz/api/services/infra/
--  label-codes.ts):
--    orchestration_plan_state — 0 unknown (never arranged) | 1 due |
--                               2 not_due
--    orchestration_plan_basis — 0 calendar | 1 data_present |
--                               2 db_missing | 3 csv_present |
--                               4 csv_missing | 5 check_failed
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
    basis           SMALLINT    NOT NULL DEFAULT 0,  -- label code (static.label_dim 'orchestration_plan_basis'): 0=calendar 1=data_present 2=db_missing 3=csv_present 4=csv_missing 5=check_failed
    missing_count   INTEGER     NOT NULL DEFAULT 0,  -- sessions the pre-check found absent from the service's table (0 = none / calendar rule)
    missing_detail  JSONB,                          -- the pre-check's session lists (capped sample)
    last_success_at TIMESTAMPTZ,                     -- the arrangement's input snapshot (max normal-exit ended_at)
    due_at          TIMESTAMPTZ,                     -- freq window re-open (last_success_at + period); NULL = every pass / once / no window
    arranged_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT pk_orchestration_task_state PRIMARY KEY (service)
);

COMMENT ON TABLE  monitor.orchestration_task_state                     IS 'Per-orchestration-service arrangement state: the maintainer-driven run/don''t-run decision (due / not_due) computed by the monitor area''s OrchestrationArranger — from the service_config freq window vs the last successful exit, or from the service''s declared data pre-check (the evidence is the basis column). The nightly chain arranges each tier before running it (not-due steps are skipped, not spawned); the Monitor UI''s Orchestration page shows the arrangement (running / queue / no-need).';
COMMENT ON COLUMN monitor.orchestration_task_state.service             IS 'The tier step''s dotted python -m module path — the key the service_config tree and the heartbeat/audit tables hang off. One row per service (first tier occurrence when a service appears in more than one tier file).';
COMMENT ON COLUMN monitor.orchestration_task_state.tier                IS 'The orchestration tier file stem the step belongs to (downloads_daily, analysis_daily, ...).';
COMMENT ON COLUMN monitor.orchestration_task_state.step                IS 'The step''s numeric key within its tier file (display order context for the board).';
COMMENT ON COLUMN monitor.orchestration_task_state.freq                IS 'The effective freq from the service_config static tree (the value the arrangement was computed with — the own file''s, else the default.json fallback''s).';
COMMENT ON COLUMN monitor.orchestration_task_state.priority            IS 'The service''s service_config priority (the orchestration give-way rank; the queue section sorts by it — downloads outrank non-downloads, then frequency).';
COMMENT ON COLUMN monitor.orchestration_task_state.state               IS 'Arrangement verdict — SMALLINT label code (static.label_dim ''orchestration_plan_state''): 0 = unknown (never arranged), 1 = due (run it in the arranged pass — the freq window re-opened, or the data pre-check found work), 2 = not_due (the freq window holds, once + a success ever, or the pre-check found no work — the pass skips the step).';
COMMENT ON COLUMN monitor.orchestration_task_state.basis               IS 'The evidence behind the verdict — SMALLINT label code (static.label_dim ''orchestration_plan_basis''): 0 = calendar (no data pre-check declared — the freq-calendar rule decided), 1 = data_present (every expected session is in the service''s data table), 2 = db_missing (a session is absent from that table and no source-csv stage applies — the service runs to produce it), 3 = csv_present (absent from the table but its source csv is downloaded — a download skips, its build loads), 4 = csv_missing (absent and not downloaded either — a download runs, a build waits for it), 5 = check_failed (the pre-check itself errored — the service runs fail-open).';
COMMENT ON COLUMN monitor.orchestration_task_state.missing_count        IS 'How many expected sessions the data pre-check found absent from the service''s data table — the sessions the verdict turns on (to fetch / to load / absent). 0 = none, or the calendar rule decided.';
COMMENT ON COLUMN monitor.orchestration_task_state.missing_detail       IS 'The data pre-check''s session lists as JSON: the probed window, the absent sessions split by whether their source csv is staged, or the error for check_failed. NULL when the calendar rule decided.';
COMMENT ON COLUMN monitor.orchestration_task_state.last_success_at     IS 'The arrangement''s input snapshot: the service''s last normal-exit ended_at from monitor.service_audit at arrange time.';
COMMENT ON COLUMN monitor.orchestration_task_state.due_at              IS 'When the freq window re-opens (last_success_at + period). NULL = no window to wait out: never succeeded (due now), or a no-period freq (occasionally / on_demand), or once (satisfied by any success — the state column carries the verdict). Residents (is_resident) hold no row here at all.';
COMMENT ON COLUMN monitor.orchestration_task_state.arranged_at         IS 'When the arranger wrote this verdict (the board''s success-since-arranged demotion keys off it).';

-- The board's queue scan: due rows awaiting their pass.
CREATE INDEX IF NOT EXISTS idx_orchestration_task_state_due
    ON monitor.orchestration_task_state (priority DESC, service)
    WHERE state = 1;
