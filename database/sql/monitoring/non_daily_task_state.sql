-- ============================================================================
--  Non-daily task state — the completeness/backfill state of ONE non-daily
--  orchestration task (monthly / quarterly / semiannually / once tiers),
--  one row per service.
--
--  The non-daily tiers ride the nightly chain but their own cadence guards
--  make off-cadence runs no-ops (quarterly compositions refresh only at
--  quarter start, semiannual dividends at half-year boundaries), so a
--  FAILED cadence window leaves the task's related data missing for weeks
--  — the nightly self-heal cannot see it. The monitor area's NonDailyOps
--  (server_cluster/monitor/non_daily.py) evaluates each configured task
--  against the DAILY DATA IDENTITIES (the universe the daily pipelines say
--  exists: stats.index_identity / stats.etf_identity / stats.stock_identity
--  latest-date codes vs the task's loaded data) and upserts the verdict
--  here; the maintainer's Saturday backfill job (server_cluster/maintainer/
--  non_daily.py) re-runs missing tasks weekly until the data is loaded.
--
--  Re-run scheduling: next_check_at = the NEXT Saturday 05:00 Asia/Shanghai
--  when a check finds missing data, +7d after each re-run attempt ("if
--  still none, one week is the query interval, until all data is done
--  loaded" — no attempt cap); NULL when the check is complete.
--
--  state is a SMALLINT label code (the label-code contract, static.
--  label_dim + _common/labels.py + data_viz/api/services/infra/
--  label-codes.ts):
--    non_daily_state — 0 unknown (never checked) | 1 complete |
--                      2 missing | 3 check_failed
--
--  The per-run trail is NOT here: every re-run is a real run-registry
--  child whose monitor.service_audit row (plus the task's own heartbeat)
--  already records started/ended/exit. This table is the CURRENT state
--  (upsert, not append) — attempts is just the consecutive-missing
--  backfill counter (reset by a passing check).
--
--  Check configs live in the git-reviewed static tree
--  database/sql/monitoring/static/non_daily_checks/<dotted.service>.json
--  (read-only like external_request_config — no UI write path).
--
--  Applied via monitoring/00_init.sql.
-- ============================================================================

CREATE TABLE IF NOT EXISTS monitor.non_daily_task_state (
    service        TEXT        NOT NULL,
    tier           TEXT        NOT NULL,
    state          SMALLINT    NOT NULL DEFAULT 0,  -- label code (static.label_dim 'non_daily_state'): 0=unknown 1=complete 2=missing 3=check_failed
    check_kind     TEXT,      -- db_universe_coverage | db_source_freshness | snapshot_fresh | months_present | NULL = no check configured (schedule-only monitoring)
    missing_count  INTEGER    NOT NULL DEFAULT 0,
    missing_detail JSONB,     -- sample of what is missing (codes / dates / files), capped by the checker
    attempts       INTEGER    NOT NULL DEFAULT 0,   -- consecutive-missing backfill re-runs driven off this row
    last_check_at  TIMESTAMPTZ,
    last_rerun_at  TIMESTAMPTZ,
    next_check_at  TIMESTAMPTZ,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT pk_non_daily_task_state PRIMARY KEY (service)
);

COMMENT ON TABLE  monitor.non_daily_task_state                IS 'Per-non-daily-task completeness + backfill state: the identity-grounded check verdict (complete / missing / check_failed), the missing sample, the weekend re-run attempt count and the next-check time (Saturday 05:00 China time while missing, +7d per attempt, NULL when complete). Written by the monitor area NonDailyOps (POST /non-daily/evaluate, the Saturday backfill); read by GET /api/monitor/non-daily (the Service Monitor Non-daily page).';
COMMENT ON COLUMN monitor.non_daily_task_state.service       IS 'The task''s dotted python -m module path (the orchestration tier step''s script) — the key the check-config tree and the service_config tree hang off.';
COMMENT ON COLUMN monitor.non_daily_task_state.tier          IS 'The orchestration tier file stem the step belongs to (downloads_quarterly, builds_monthly, ...).';
COMMENT ON COLUMN monitor.non_daily_task_state.state         IS 'Completeness verdict — SMALLINT label code, domain ''non_daily_state'' (static.label_dim): 0 = unknown (never checked), 1 = complete (related data loaded vs the daily identities), 2 = missing (identity-grounded gaps found — the backfill''s charge), 3 = check_failed (the check itself errored; surfaced, never re-run blind).';
COMMENT ON COLUMN monitor.non_daily_task_state.check_kind    IS 'The configured check kind: db_universe_coverage (identity codes missing from the task''s target table), db_source_freshness (target table''s per-source last_updated age), snapshot_fresh (temps snapshot file age), months_present (expected month dirs on disk). NULL = no check configured — the task is schedule/last-run monitored only.';
COMMENT ON COLUMN monitor.non_daily_task_state.missing_count  IS 'How many identity codes / dates / months / files the check found missing (0 when complete).';
COMMENT ON COLUMN monitor.non_daily_task_state.missing_detail IS 'A capped JSON sample of the missing items ({codes: [...]} / {months: [...]} / {files: [...]} / {error: ...} for check_failed) — enough to eyeball the gap, never the full list.';
COMMENT ON COLUMN monitor.non_daily_task_state.attempts      IS 'Weekend backfill re-runs driven since the task went missing (reset to 0 by a passing check). No cap: weekly until the data is done loaded.';
COMMENT ON COLUMN monitor.non_daily_task_state.last_check_at  IS 'When NonDailyOps last evaluated this task.';
COMMENT ON COLUMN monitor.non_daily_task_state.last_rerun_at  IS 'When the backfill last submitted this task (module + tier args + rerun_args through the run registry).';
COMMENT ON COLUMN monitor.non_daily_task_state.next_check_at  IS 'The backfill''s due gate: while missing, the next Saturday 05:00 Asia/Shanghai (then +7d per attempt); NULL when complete / unknown — no re-run is due.';

-- The backfill's due scan: missing rows whose next_check has arrived.
CREATE INDEX IF NOT EXISTS idx_non_daily_task_state_next_check
    ON monitor.non_daily_task_state (next_check_at)
    WHERE next_check_at IS NOT NULL;
