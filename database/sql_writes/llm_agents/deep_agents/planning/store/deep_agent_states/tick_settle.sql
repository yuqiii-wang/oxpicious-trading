-- SQL write action — llm.deep_agent_states UPDATE (settle a tick:
-- completed | failed). Executed by llm_agents/deep_agents/planning/store.py
-- (PlanStore.tick_step, the non-in_progress branch): the hop finished —
-- finished_at stamps now, the tick note rides the result context minted
-- just before (result_context_id), and *error* (the failed settle's
-- reason) lands COALESCEd — an existing error is never blanked.
--
-- Params (asyncpg $n, in order):
--   $1 state_id           BIGINT — the row settling
--   $2 status             TEXT — 'completed' | 'failed' (validated
--                         host-side against TICK_STATUSES)
--   $3 result_context_id  BIGINT | NULL — the tick note's context (None
--                         when the tick carried no note)
--   $4 error              TEXT | NULL — the failure reason
UPDATE llm.deep_agent_states SET status = $2,
finished_at = now(),
result_context_id = COALESCE($3, result_context_id),
error = COALESCE($4, error)
WHERE state_id = $1
