-- SQL write action — llm.deep_agent_states UPDATE (settle a child
-- state). Executed by llm_agents/deep_agents/planning/store.py
-- (PlanStore.finish_child_state): the subagent's final answer (or
-- error) already landed as its subagent-result context — this points
-- the row at it and closes it, COALESCEd so an existing error is never
-- blanked.
--
-- Params (asyncpg $n, in order):
--   $1 state_id           BIGINT — the child row settling
--   $2 status             TEXT — 'completed' | 'failed' (validated
--                         host-side)
--   $3 result_context_id  BIGINT — the minted subagent-result context
--   $4 error              TEXT | NULL — the failure reason
UPDATE llm.deep_agent_states SET status = $2, finished_at = now(),
result_context_id = COALESCE($3, result_context_id),
error = COALESCE($4, error) WHERE state_id = $1
