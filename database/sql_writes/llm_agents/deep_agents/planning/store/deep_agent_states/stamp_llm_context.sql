-- SQL write action — llm.deep_agent_states UPDATE (point plan-step
-- states at their node's ONE context id). Executed by
-- llm_agents/deep_agents/planning/store.py (PlanStore.stamp_states_context):
-- the execute node stamps its whole plan at entry (the agent loop walks
-- the steps inside its single conversation). Idempotent: rows that
-- already carry a context (a re-tick after a re-stamp) keep theirs.
--
-- Params (asyncpg $n, in order):
--   $1 state_ids       BIGINT[] — the plan's step rows
--   $2 llm_context_id  TEXT — the execute node's ONE chained context
--                      (uuid hex; links the rows to the conversation's
--                      llm.llm_requests rows)
UPDATE llm.deep_agent_states SET llm_context_id = $2
WHERE state_id = ANY($1::bigint[]) AND llm_context_id IS NULL
