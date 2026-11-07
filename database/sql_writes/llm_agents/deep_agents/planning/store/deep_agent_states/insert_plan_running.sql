-- SQL write action — llm.deep_agent_states INSERT (the run's RUNNING
-- PLANNING NODE). Executed by llm_agents/deep_agents/planning/store.py
-- (PlanStore.begin_thread) at RUN START, before the plan LLM call: the
-- ordinal-0 top-level state row (node 'plan', in_progress) that makes
-- the run visible from its first instant (the summary history's source,
-- so the Generate click's record exists immediately).
--
-- Params (asyncpg $n, in order):
--   $1 thread_id        TEXT — the plan thread's id (the chain root:
--                       its rows carry their own id as super_thread_id)
--   $2 super_thread_id  TEXT | NULL — the CHAIN id (legacy single-thread
--                       runs pass None and read as their own chain)
--   $3 node             TEXT — the 'plan' work tag (store.PLAN_NODE)
--   $4 title            TEXT — the run title (capped host-side)
--
-- Returns the new state_id (the runner stamps the plan node's LLM
-- context onto the row). The plan node closes the row via
-- settle_plan_node; a stop mid-planning leaves it to the chain settle.
INSERT INTO llm.deep_agent_states
    (thread_id, super_thread_id, node, title, status, ordinal,
     started_at)
VALUES ($1, $2, $3, $4, 'in_progress', 0, now())
RETURNING state_id
