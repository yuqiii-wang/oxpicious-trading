-- SQL write action — llm.deep_agent_states INSERT (one PENDING step
-- added AT RUNTIME). Executed by llm_agents/deep_agents/planning/store.py
-- (PlanStore.add_plan_states — the plan-update tool's write): a
-- mid-run investigation add, main-lane (parent NULL, ordinal from the
-- shifted gap) or under a fork (parent set, ordinal 0 — sibling order
-- is state_id). The chain id inherits from the thread's existing rows
-- when the caller (a plan tool) does not know it.
--
-- Params (asyncpg $n, in order):
--   $1 thread_id        TEXT — the owning thread
--   $2 super_thread_id  TEXT | NULL — the chain id (derived host-side
--                       from the thread's rows when None)
--   $3 parent_state_id  BIGINT | NULL — the fork parent (None = top
--                       level)
--   $4 node             TEXT — the step's node key
--   $5 title            TEXT — the step's title (capped host-side)
--   $6 ordinal          INTEGER — top-level plan order, or 0 under a
--                       fork
--   $7 input_context_id BIGINT | NULL — the step-input context minted
--                       for the notes/goal payload
--   $8 llm_context_id   TEXT | NULL — the conversation that owns the
--                       new node (main execute context for main-lane
--                       adds, the subagent's own for fork extensions)
--
-- Returns the full snapshot row (the _STATE_COLUMNS list).
INSERT INTO llm.deep_agent_states
    (thread_id, super_thread_id, parent_state_id, node,
     title, ordinal, input_context_id, llm_context_id)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
RETURNING state_id, thread_id, parent_state_id, node, title, status, ordinal, input_context_id, result_context_id, error, started_at, finished_at, llm_context_id
