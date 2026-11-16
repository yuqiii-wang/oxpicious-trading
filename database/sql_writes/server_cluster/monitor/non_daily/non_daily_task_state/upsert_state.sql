-- SQL write action — monitor.non_daily_task_state UPSERT (one task's
-- evaluate verdict). Executed by server_cluster/monitor/non_daily.py
-- (NonDailyOps._upsert_state — every check verdict lands here; the
-- passing check resets the backfill attempt counter).
--
-- next_check_at semantics (the weekly retry gate): a FRESH missing
-- verdict sets the next Saturday 05:00 China-time gate; a task ALREADY
-- missing with a pending gate KEEPS it — the gate bounds the interval
-- between re-run ATTEMPTS, so an evaluate pass must never push a
-- scheduled attempt into the future (the Saturday drive would then
-- never find anything due). record_rerun.sql owns the +7d bump after
-- each attempt.
--
-- Params (asyncpg $n, in order):
--   $1 service        TEXT        — the task's dotted python -m module
--   $2 tier           TEXT        — the orchestration tier file stem
--   $3 state          SMALLINT    — label code 'non_daily_state'
--                                  (0 unknown | 1 complete | 2 missing |
--                                   3 check_failed)
--   $4 check_kind     TEXT        — the configured check kind (NULL =
--                                   no check configured)
--   $5 missing_count  INTEGER     — how many items the check found missing
--   $6 missing_detail JSONB       — capped sample of the missing items
--   $7 next_check_at  TIMESTAMPTZ — the fresh Saturday gate (used only
--                                   when no pending gate applies; NULL
--                                   when complete)
INSERT INTO monitor.non_daily_task_state
    (service, tier, state, check_kind, missing_count, missing_detail,
     attempts, last_check_at, next_check_at, updated_at)
VALUES ($1::text, $2::text, $3::smallint, $4::text, $5::integer,
        $6::jsonb, 0, now(), $7::timestamptz, now())
ON CONFLICT (service) DO UPDATE SET
    tier           = EXCLUDED.tier,
    state          = EXCLUDED.state,
    check_kind     = EXCLUDED.check_kind,
    missing_count  = EXCLUDED.missing_count,
    missing_detail = EXCLUDED.missing_detail,
    attempts       = CASE WHEN EXCLUDED.state = 2
                          THEN monitor.non_daily_task_state.attempts
                          ELSE 0 END,
    last_check_at  = now(),
    next_check_at  = CASE
                       -- still missing with a pending gate: the attempt
                       -- stays scheduled where it is
                       WHEN EXCLUDED.state = 2
                            AND monitor.non_daily_task_state.state = 2
                            AND monitor.non_daily_task_state.next_check_at
                                IS NOT NULL
                         THEN monitor.non_daily_task_state.next_check_at
                       ELSE EXCLUDED.next_check_at
                     END,
    updated_at     = now()
