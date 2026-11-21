-- ============================================================================
--  Caution log — the ONE cross-service caution trail: any running service
--  hitting an error, any AI run flagging something suspicious, and any
--  manually-noted pending bug land here, surfaced by the Monitor UI's
--  Cautions tab (GET /api/monitor/cautions).
--
--  One row per OPEN issue per service (the dedupe contract below), not a
--  raw error feed — per-request failures keep their lean homes
--  (monitor.external_resource_audit, llm.llm_requests) and per-run lives
--  keep theirs (monitor.service_audit). A caution row is the WORKING
--  item: context-rich, severity-tagged, resolvable.
--
--  Writers (all fire-and-forget — a caution write may never fail or slow
--  the code path it audits):
--    Python — _common/caution_log.py (bounded queue + one daemon writer
--      thread per process, the external_resource_audit shape):
--        _common/service_heartbeat.py    abnormal process stop (every
--                                        pipeline / batch / daemon run)
--        server_cluster/monitor/
--          orchestration.py              orchestration tier step failures
--        server_cluster/maintainer/
--          jobs.py / pipeline.py         maintenance job + nightly-tier
--                                        failures
--        llm_agents/deep_agents/
--          core/output_validation.py     output complaints surviving the
--                                        correction round
--          planning/store/outputs.py     [CRITICAL]-flagged findings
--          langgraph/decision/engine.py  jev decision host failures +
--                                        below-floor confidence
--        manual — python -m _common.caution_log (a pending bug found
--                 while running anything by hand; the dev skill)
--    TypeScript — data_viz/api/services/caution-log.service.ts (the API's
--      central error handler + the py-runner cluster-unreachable and
--      deep-agents-daemon failure branches).
--
--  DEDUPE CONTRACT: UNIQUE (service, dedupe_key) over OPEN rows only
--  (the partial unique index). Writers upsert ON CONFLICT ... WHERE NOT
--  is_resolved, bumping occurrence_count / last_seen_at (and refreshing
--  title/detail/severity) — a recurring issue stays ONE open row.
--  Resolving (is_resolved = true) closes the row; a RECURRENCE after
--  resolve inserts a fresh row, signalling a resolved issue came back.
--  A resolved row is history: it is never re-opened, only re-raised.
--
--  Type columns are SMALLINT label codes (the label-code contract,
--  static.label_dim + _common/labels.py + data_viz/api/services/
--  label-codes.ts):
--    source         — domain 'caution_source':   0 service | 1 ai | 2 manual
--    severity_level — domain 'caution_severity': 0 info | 1 warning |
--                                                     2 error | 3 critical
--  service stays TEXT on purpose: open-ended vocabulary (same choice as
--  monitor.service_audit.service).
--
--  DATE SEMANTICS: `date` is the GMT (UTC) day of created_at — a new
--  date starts at Beijing 08:00 (GMT midnight), same as
--  monitor.external_resource_audit. Retention: the maintainer's
--  LogCleanupJob purges rows resolved more than 180 days ago; open rows
--  are never purged.
--
--  Applied before db_health.sql (no FKs; alphabetical order).
-- ============================================================================

CREATE TABLE IF NOT EXISTS monitor.caution_log (
    id                 bigserial    NOT NULL,
    -- rolling-deploy insurance, same as external_resource_audit.service
    service            text         NOT NULL DEFAULT 'unknown',  -- writing process's service name (dotted module path) or the named area (e.g. 'data_viz.api', 'orchestration')
    source             SMALLINT     NOT NULL,  -- label code (static.label_dim 'caution_source'): 0 service | 1 ai | 2 manual
    severity_level     SMALLINT     NOT NULL,  -- label code (static.label_dim 'caution_severity'): 0 info | 1 warning | 2 error | 3 critical
    title              text         NOT NULL,  -- one-line issue summary (table-row text; NOT the full context)
    detail             jsonb        NOT NULL DEFAULT '{}'::jsonb,  -- the logged CONTEXT: error_tail / traceback / step / chain_id / scope… free-form
    dedupe_key         text         NOT NULL,  -- stable per-service issue signature — the dedupe contract's key; recurrence of an OPEN issue matches it
    occurrence_count   integer      NOT NULL DEFAULT 1,   -- times this issue hit while the row stayed open (dedupe bumps it)
    is_resolved        boolean      NOT NULL DEFAULT false,
    created_at         timestamptz  NOT NULL DEFAULT now(),  -- when the issue FIRST hit (row insert time)
    last_seen_at       timestamptz  NOT NULL DEFAULT now(),  -- when the issue LAST hit (dedupe bump time)
    resolved_timestamp timestamptz,                          -- NULL while open; set by POST /api/monitor/cautions/resolve
    date               date GENERATED ALWAYS AS ((created_at AT TIME ZONE 'GMT')::date) STORED,
    PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS caution_log_created_idx
    ON monitor.caution_log (created_at DESC);

CREATE INDEX IF NOT EXISTS caution_log_date_idx
    ON monitor.caution_log (date);

-- The tab's default view: OPEN rows, worst severity first, most recent hit
-- first.
CREATE INDEX IF NOT EXISTS caution_log_open_idx
    ON monitor.caution_log (severity_level DESC, last_seen_at DESC)
    WHERE NOT is_resolved;

-- The dedupe contract: at most ONE OPEN row per (service, dedupe_key).
-- Partial (open rows only) so a resolved issue may re-raise as a new row.
CREATE UNIQUE INDEX IF NOT EXISTS caution_log_open_dedupe_idx
    ON monitor.caution_log (service, dedupe_key)
    WHERE NOT is_resolved;

COMMENT ON TABLE monitor.caution_log IS
    'Cross-service caution trail: one OPEN row per issue per service — any '
    'running service hitting an error, any AI run flagging something '
    'suspicious, any manually-noted pending bug. Writers upsert ON CONFLICT '
    '(service, dedupe_key) WHERE NOT is_resolved (bumping occurrence_count '
    '/ last_seen_at), so a recurring issue stays one open row; resolving '
    'closes it and a recurrence opens a fresh row. source / severity_level '
    'are SMALLINT label codes (static.label_dim domains caution_source / '
    'caution_severity). Written fire-and-forget by '
    '_common/caution_log.py (Python) and '
    'data_viz/api/services/caution-log.service.ts (TS); read by the '
    'Monitor UI Cautions tab. Resolved rows are purged after 180 days by '
    'the maintainer LogCleanupJob.';

COMMENT ON COLUMN monitor.caution_log.service IS
    'The service the caution is ABOUT — the writing process''s service '
    'name (dotted module path, the service_audit vocabulary) when the '
    'process reports itself, or the named area for cross-process writers '
    '(orchestration, a maintenance job, the data_viz API, an llm_agents '
    'daemon). Stays TEXT on purpose: open-ended vocabulary (label-code '
    'contract exception).';

COMMENT ON COLUMN monitor.caution_log.source IS
    'Where the caution came from, as its static.label_dim '
    '(''caution_source'' domain) SMALLINT code: 0 service (a running '
    'service / pipeline / job hit an error), 1 ai (an AI run found '
    'something suspicious — output-validation complaints, [CRITICAL] '
    'findings, below-floor jev decisions), 2 manual (a pending bug noted '
    'by hand via python -m _common.caution_log).';

COMMENT ON COLUMN monitor.caution_log.severity_level IS
    'How bad the caution is, as its static.label_dim (''caution_severity'' '
    'domain) SMALLINT code — code order is semantic, best -> worst: '
    '0 info, 1 warning, 2 error, 3 critical (an AI-flagged [CRITICAL] '
    'finding). Service errors default to 2, AI suspicions to 1, manual '
    'entries choose their own.';

COMMENT ON COLUMN monitor.caution_log.title IS
    'One-line issue summary — the table-row text in the Cautions tab '
    '(truncated at write time). The full context lives in detail.';

COMMENT ON COLUMN monitor.caution_log.detail IS
    'The logged CONTEXT, free-form JSONB: error_tail / traceback for '
    'service errors, step / script / exit_code for orchestration '
    'failures, chain_id / node for AI findings, whatever a manual entry '
    'passes. Never the whole log — tails and signatures only.';

COMMENT ON COLUMN monitor.caution_log.dedupe_key IS
    'Stable per-service issue signature (e.g. the exception class, the '
    'orchestration step key, the jev lane) — the dedupe contract''s key: '
    'UNIQUE (service, dedupe_key) over OPEN rows only, so a recurring '
    'issue bumps its one open row instead of flooding the table, and a '
    'resolved issue re-raising inserts a fresh row.';

COMMENT ON COLUMN monitor.caution_log.occurrence_count IS
    'How many times the issue hit while this row stayed open — the upsert '
    'bumps it (1 = seen once). occurrence_count > 1 means a recurring '
    'issue.';

COMMENT ON COLUMN monitor.caution_log.is_resolved IS
    'FALSE while the issue is open (the working set the Cautions tab '
    'shows by default). Set TRUE only via the resolve action '
    '(POST /api/monitor/cautions/resolve), which stamps '
    'resolved_timestamp. Never un-set — a recurrence opens a NEW row.';

COMMENT ON COLUMN monitor.caution_log.created_at IS
    'When the issue FIRST hit (the row''s insert time).';

COMMENT ON COLUMN monitor.caution_log.last_seen_at IS
    'When the issue LAST hit — bumped by each deduped recurrence, so an '
    'old open row with a recent last_seen_at is a STILL-live issue.';

COMMENT ON COLUMN monitor.caution_log.resolved_timestamp IS
    'NULL while the issue is open. Set (with is_resolved = TRUE) by the '
    'resolve action from the Cautions tab.';

COMMENT ON COLUMN monitor.caution_log.date IS
    'GENERATED: created_at bucketed to a GMT (UTC) day — a new date '
    'starts at Beijing 08:00 (GMT midnight), so the Beijing 00:00–08:00 '
    'overnight tail counts to the previous operational day. Writers '
    'cannot set it.';
