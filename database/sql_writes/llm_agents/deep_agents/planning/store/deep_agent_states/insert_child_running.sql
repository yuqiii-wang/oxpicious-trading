-- SQL write action — llm.deep_agent_states INSERT (a spawned subagent's
-- child state). Executed by llm_agents/deep_agents/planning/store.py
-- (PlanStore.create_child_state): the child is already running under
-- the step that was in_progress at spawn time (LangGraph's subgraph);
-- ordinal 0 under the parent (sibling order is state_id). The child
-- mints its OWN llm_context_id (its conversation is not the parent
-- node's).
--
-- Params (asyncpg $n, in order):
--   $1 thread_id        TEXT — the owning (custom) thread
--   $2 super_thread_id  TEXT | NULL — the chain id (derived host-side
--                       from the thread's rows)
--   $3 parent_state_id  BIGINT — the fork parent's state row
--   $4 node             TEXT — the fork's node slug
--   $5 title            TEXT — the child's title
--   $6 llm_context_id   TEXT | NULL — the child conversation's ONE
--                       chained context
--
-- Returns the new state_id (the registry item lands in the store when
-- the output hook settles the child).
INSERT INTO llm.deep_agent_states
    (thread_id, super_thread_id, parent_state_id, node, title,
     status, ordinal, started_at, llm_context_id)
VALUES ($1, $2, $3, $4, $5, 'in_progress', 0, now(), $6)
RETURNING state_id
