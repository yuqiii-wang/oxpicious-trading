-- SQL write action — llm.deep_agent_states UPDATE (stamp a step START).
-- Executed by llm_agents/deep_agents/planning/store.py (PlanStore
-- .tick_step, status 'in_progress'): the hop's agent began working the
-- node. Idempotent on the timestamp — a re-tick after a reopen keeps
-- the FIRST start time.
--
-- Params (asyncpg $n, in order):
--   $1 state_id  BIGINT — the row going in_progress
UPDATE llm.deep_agent_states SET status = 'in_progress',
started_at = COALESCE(started_at, now()) WHERE state_id = $1
