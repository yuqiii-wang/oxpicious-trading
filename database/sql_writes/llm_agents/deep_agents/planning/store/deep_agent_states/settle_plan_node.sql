-- SQL write action — llm.deep_agent_states UPDATE (close the running
-- planning node). Executed by llm_agents/deep_agents/planning/store.py
-- (PlanStore.settle_plan_state) when the plan phase ends: completed with
-- the step-count note, or failed with the proposal/persistence reason.
--
-- Params (asyncpg $n, in order):
--   $1 thread_id  TEXT — the plan thread's id
--   $2 status     TEXT — 'completed' | 'failed' (validated host-side)
--   $3 error      TEXT | NULL — the failed settle's reason
--   $4 node       TEXT — the 'plan' work tag (store.PLAN_NODE)
--
-- Only an in_progress planning row moves (idempotent — the blanket
-- finalize/stop settle owns the row once this could not); returns the
-- state_id so the caller attaches the note context to it.
UPDATE llm.deep_agent_states SET status = $2,
finished_at = now(), error = $3
WHERE thread_id = $1 AND node = $4 AND parent_state_id IS NULL
AND status = 'in_progress' RETURNING state_id
