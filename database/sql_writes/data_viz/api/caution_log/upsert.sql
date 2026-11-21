-- SQL write action — monitor.caution_log INSERT .. ON CONFLICT (upsert).
-- Executed by data_viz/api/services/caution-log.service.ts (recordCaution):
-- the data_viz API's own cautions (the central error handler, the
-- py-runner cluster-unreachable branches, the deep-agents daemon
-- failures). The Python twin of this statement lives inline in
-- _common/caution_log.py (psycopg %s form) — two executors, one contract.
--
-- DEDUPE: the partial unique index caution_log_open_dedupe_idx
-- (UNIQUE (service, dedupe_key) WHERE NOT is_resolved) matches the open
-- row — a recurrence bumps occurrence_count / last_seen_at on it; after
-- a resolve the insert falls through to a fresh row.
--
-- Params (node-postgres $n, in order):
--   $1 service        TEXT    — the service/area the caution is about
--   $2 source         SMALLINT — caution_source code: 0 service | 1 ai | 2 manual
--   $3 severity_level SMALLINT — caution_severity code: 0 info | 1 warning | 2 error | 3 critical
--   $4 title          TEXT    — one-line issue summary
--   $5 detail         JSONB   — the context object ('{}' when none)
--   $6 dedupe_key     TEXT    — stable per-service issue signature
INSERT INTO monitor.caution_log
    (service, source, severity_level, title, detail, dedupe_key)
VALUES ($1::text, $2::smallint, $3::smallint, $4::text,
        $5::jsonb, $6::text)
ON CONFLICT (service, dedupe_key) WHERE NOT is_resolved DO UPDATE SET
    severity_level   = EXCLUDED.severity_level,
    title            = EXCLUDED.title,
    detail           = EXCLUDED.detail,
    occurrence_count = monitor.caution_log.occurrence_count + 1,
    last_seen_at     = now()
