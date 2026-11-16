-- SQL write action — llm.deep_agent_states INSERT (the RUN-NODE FORK's
-- main-line row, 2026-11-15). Executed by
-- llm_agents/deep_agents/planning/store/states.py
-- (PlanStore.create_toplevel_fork) from the deep-agents daemon's
-- node-keyed fork action: a fork fired off a STATE-LESS runner-lane
-- seat (the output node) continues THE MAIN LINE — the child lands as
-- a TOP-LEVEL row on the OWNING segment's thread (parent NULL,
-- ordinal after the last top-level row), child_kind 1 (agent_child_kind
-- fork), so the canvas's lane walk renders it in the lane and the
-- pre-fork failed attempt (its kind-2 old-chain stub) hangs as the
-- side branch.
--
-- Params (asyncpg $n, in order):
--   $1 thread_id        TEXT — the OWNING segment thread
--   $2 super_thread_id  TEXT | NULL — the chain id (derived host-side)
--   $3 node             TEXT — the fork child's node slug (fork_…)
--   $4 title            TEXT — the child's title (the directive)
--   $5 llm_context_id   TEXT — the child conversation's ONE chained
--                       context
--
-- Returns the new state_id.
INSERT INTO llm.deep_agent_states
    (thread_id, super_thread_id, parent_state_id, node, title,
     status, ordinal, started_at, llm_context_id, child_kind)
VALUES ($1, $2, NULL, $3, $4, 'in_progress',
        COALESCE((SELECT MAX(ordinal) FROM llm.deep_agent_states
                  WHERE thread_id = $1 AND parent_state_id IS NULL), 0) + 1,
        now(), $5, 1)
RETURNING state_id
