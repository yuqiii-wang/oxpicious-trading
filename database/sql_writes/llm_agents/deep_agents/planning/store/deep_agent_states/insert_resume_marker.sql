-- SQL write action — llm.deep_agent_states INSERT (the SUPERVISOR
-- FAILURE-FORK MARKER, 2026-11-15). Executed by
-- llm_agents/deep_agents/planning/store/states.py — from
-- PlanStore.reopen_state (the retry: the failed attempt the flip is
-- about to clear off the parent row) and PlanStore.record_failure_marker
-- (the no-action verdict: the failure the run proceeds past). A
-- child_kind 3 (agent_child_kind resume) RECORD row under the step —
-- the fork action on the UI: the failed attempt as a RED DEAD-END
-- BRANCH (settled failed, one dashed leg in, no merge-back), carrying
-- the prior attempt's error and the verdict note as its title; never
-- routed (child rows are not plan rows), excluded from the summary-run
-- step counts (the runs/run_dates views filter it).
--
-- Params (asyncpg $n, in order):
--   $1 thread_id        TEXT — the owning (custom) thread
--   $2 super_thread_id  TEXT | NULL — the chain id (the parent row's)
--   $3 parent_state_id  BIGINT — the re-opened step's state row
--   $4 node             TEXT — the parent's node slug (the marker is
--                       ABOUT that node)
--   $5 title            TEXT — "supervisor resume" + the verdict note
--   $6 error            TEXT | NULL — the prior attempt's error (the
--                       record of WHAT failed)
INSERT INTO llm.deep_agent_states
    (thread_id, super_thread_id, parent_state_id, node, title,
     status, ordinal, started_at, finished_at, child_kind, error)
VALUES ($1, $2, $3, $4, $5, 'failed', 0, now(), now(), 3, $6)
RETURNING state_id
