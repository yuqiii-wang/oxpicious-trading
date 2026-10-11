-- ============================================================================
--  External resource stats — per-day aggregation of
--  monitor.external_resource_audit, one row per (service, status_code,
--  date): how many external requests each process made per day, split by
--  outcome status, with how many failed and the duration profile.
--
--  COMPUTED TABLE — the audit is the source of truth; this table is
--  rebuilt from it by python -m database.external_resource_stats
--  (database/external_resource_stats.py). Incremental by default: a date
--  is stale — and recomputed (purged + re-aggregated) — when the audit
--  gained rows after the date's last computed_at (a date's rows keep
--  arriving until the GMT-midnight rollover — Beijing 08:00);
--  --force purges + rebuilds every date. Rows
--  here are NEVER hand-written and never pruned by their own runner
--  (the audit has no retention either).
--
--  Grain keys:
--    service      — the writing process's service name (TEXT, open-ended
--                   vocabulary — same as the audit table's column)
--    status_code  — raw HTTP status of the final attempt; NULL = no
--                   response at all (transport error / timeout), grouped
--                   as its own outcome bucket (NULLS NOT DISTINCT — at
--                   most one NULL bucket per (service, date))
--    date         — GENERATED GMT (UTC) day of the audit's created_at
--                   (a new date starts at Beijing 08:00 — GMT midnight)
--
--  Applied after 05_external_resource_audit.sql.
-- ============================================================================

CREATE TABLE IF NOT EXISTS monitor.external_resource_stats (
    service         text             NOT NULL,
    status_code     SMALLINT,                -- raw HTTP status; NULL = no response (transport error / timeout)
    date            date             NOT NULL,
    request_count   integer          NOT NULL,
    failed_count    integer          NOT NULL,   -- rows with is_failed (subset of request_count)
    avg_duration_ms double precision,        -- NULL when the date's bucket recorded no duration
    max_duration_ms integer,
    computed_at     timestamptz      NOT NULL DEFAULT now(),
    CONSTRAINT external_resource_stats_key
        UNIQUE NULLS NOT DISTINCT (service, status_code, date)
);

CREATE INDEX IF NOT EXISTS external_resource_stats_recent_idx
    ON monitor.external_resource_stats (date DESC, service);

COMMENT ON TABLE monitor.external_resource_stats IS
    'Per-day aggregation of monitor.external_resource_audit: one row per '
    '(service, status_code, date) with request / failure counts and the '
    'avg / max whole-call duration_ms. COMPUTED by python -m '
    'database.external_resource_stats (incremental: a date is recomputed '
    'when the audit gained rows after its last computed_at; --force '
    'rebuilds every date) — never hand-written. status_code NULL = no '
    'response at all (transport error / timeout), its own bucket via '
    'UNIQUE NULLS NOT DISTINCT.';

COMMENT ON COLUMN monitor.external_resource_stats.service IS
    'The writing process''s unique service name (dotted module path) — '
    'carried over from monitor.external_resource_audit.service; TEXT by '
    'the label-code contract''s open-ended-vocabulary exception.';

COMMENT ON COLUMN monitor.external_resource_stats.status_code IS
    'Raw HTTP status of the final attempt, carried over from '
    'monitor.external_resource_audit.status_code (a measured value, not a '
    'label code). NULL = no response at all — transport error, timeout, '
    'DNS failure.';

COMMENT ON COLUMN monitor.external_resource_stats.date IS
    'The audit rows'' GMT (UTC) day (monitor.external_resource_audit.date, '
    'GENERATED from created_at — a new date starts at Beijing 08:00, so '
    'the Beijing 00:00–08:00 overnight tail counts to the previous day).';

COMMENT ON COLUMN monitor.external_resource_stats.request_count IS
    'External requests in the (service, status_code, date) bucket — one '
    'audit row per logical request, retries not itemized.';

COMMENT ON COLUMN monitor.external_resource_stats.failed_count IS
    'Requests that did not yield a usable response (audit is_failed) — '
    'always a subset of request_count.';

COMMENT ON COLUMN monitor.external_resource_stats.avg_duration_ms IS
    'Mean whole-call duration (retries + backoff included) of the '
    'bucket''s rows; NULL when none of them recorded a duration.';

COMMENT ON COLUMN monitor.external_resource_stats.max_duration_ms IS
    'Slowest whole-call duration in the bucket; NULL when none recorded '
    'a duration.';

COMMENT ON COLUMN monitor.external_resource_stats.computed_at IS
    'When the runner last (re)wrote this row — the staleness marker the '
    'incremental refresh compares against the audit''s max(created_at) '
    'per date.';
