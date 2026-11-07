-- SQL write action — llm.deep_agent_states UPDATE (rewrite a not-yet-run
-- step's title). Executed by llm_agents/deep_agents/planning/store.py
-- (PlanStore.revise_pending_state — the plan-update tool's revise op):
-- only pending rows move (a step that started is history).
--
-- Params (asyncpg $n, in order):
--   $1 state_id  BIGINT — the row to revise
--   $2 title     TEXT | NULL — the new title (NULL keeps the current
--                one — COALESCE; the revise may touch only notes)
--
-- Returns the full snapshot row; no row (gone or not pending) = the
-- caller reports the revise as a no-op.
UPDATE llm.deep_agent_states SET title = COALESCE($2, title)
WHERE state_id = $1 AND status = 'pending'
RETURNING state_id, thread_id, parent_state_id, node, title, status, ordinal, input_context_id, result_context_id, error, started_at, finished_at, llm_context_id
