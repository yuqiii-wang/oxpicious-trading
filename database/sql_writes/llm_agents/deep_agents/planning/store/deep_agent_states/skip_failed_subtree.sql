-- SQL write action — llm.deep_agent_states UPDATE (settle a failed
-- fork's pending subtree). Executed by llm_agents/deep_agents/planning/
-- store.py (PlanStore.finish_child_state, status 'failed'): a FAILED
-- child ends its fork path — every pending node the subagent added
-- under this fork settles skipped 'not run — its fork path failed' (the
-- grey tail on the thread canvas), the same settle finalize would apply
-- but immediate, so a dead fork's subtree never reads as to-be-run.
--
-- Params (asyncpg $n, in order):
--   $1 state_id  BIGINT — the failed fork's own row
--
-- The RECURSIVE CTE walks parent_state_id to the leaves (a sub-sub
-- fork's own children included); the failed row itself is excluded
-- (it just settled via settle_child). RETURNING tells the caller
-- whether any tail was actually settled (its STATES_SETTLED event).
WITH RECURSIVE subtree AS (
  SELECT state_id FROM llm.deep_agent_states
  WHERE state_id = $1
  UNION ALL
  SELECT c.state_id FROM llm.deep_agent_states c
  JOIN subtree p ON c.parent_state_id = p.state_id
)
UPDATE llm.deep_agent_states s SET status = 'skipped',
finished_at = now(),
error = 'not run — its fork path failed'
FROM subtree t WHERE s.state_id = t.state_id
AND s.status = 'pending' AND s.state_id <> $1
RETURNING s.state_id
