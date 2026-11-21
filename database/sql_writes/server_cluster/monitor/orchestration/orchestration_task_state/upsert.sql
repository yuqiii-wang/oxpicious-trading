-- SQL write action — monitor.orchestration_task_state UPSERT (one
-- service's arrangement verdict). Executed by
-- server_cluster/monitor/arrangement.py (OrchestrationArranger.arrange —
-- every verdict lands here; the whole universe is rewritten each pass,
-- so services dropped from the tier files keep their last row until the
-- next arrange touches them no more — stale rows are simply never read
-- by the board's universe join).
--
-- Params (asyncpg $n, in order):
--   $1 service         TEXT        — the tier step's dotted python -m module
--   $2 tier            TEXT        — the orchestration tier file stem
--   $3 step            TEXT        — the step's numeric key in the tier file
--   $4 freq            TEXT        — the effective service_config freq
--   $5 priority        INTEGER     — the service_config priority (give-way
--                                    rank; the queue section sorts by it)
--   $6 state           SMALLINT    — label code 'orchestration_plan_state'
--                                    (0 unknown | 1 due | 2 not_due)
--   $7 last_success_at TIMESTAMPTZ — the arrangement's input snapshot
--                                    (NULL when the service never exited
--                                    normally)
--   $8 due_at          TIMESTAMPTZ — freq window re-open time (NULL = no
--                                    window: never succeeded / always /
--                                    occasionally / on_demand / once)
INSERT INTO monitor.orchestration_task_state
    (service, tier, step, freq, priority, state, last_success_at,
     due_at, arranged_at, updated_at)
VALUES ($1::text, $2::text, $3::text, $4::text, $5::integer,
        $6::smallint, $7::timestamptz, $8::timestamptz, now(), now())
ON CONFLICT (service) DO UPDATE SET
    tier            = EXCLUDED.tier,
    step            = EXCLUDED.step,
    freq            = EXCLUDED.freq,
    priority        = EXCLUDED.priority,
    state           = EXCLUDED.state,
    last_success_at = EXCLUDED.last_success_at,
    due_at          = EXCLUDED.due_at,
    arranged_at     = now(),
    updated_at      = now()
