-- SQL write action — llm.deep_agent_states UPDATE (point a state row at
-- its result context). Executed by llm_agents/deep_agents/planning/store.py
-- (PlanStore.settle_plan_state) after the settle note's step-result
-- context row mints — the plan node's row links the context that
-- documents why it settled.
--
-- Params (asyncpg $n, in order):
--   $1 state_id           BIGINT — the row to repoint
--   $2 result_context_id  BIGINT — the minted context's id
UPDATE llm.deep_agent_states SET result_context_id = $2
WHERE state_id = $1
