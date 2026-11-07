-- SQL write action — llm.llm_requests DELETE (every request chain ONE
-- thread's traffic can reach). Executed by
-- llm_agents/deep_agents/planning/store.py (PlanStore.purge_thread —
-- the Python twin of the UI's thread delete, the same order
-- 09_deep_agent_runs_prune.sql follows): the thread's request chains
-- resolve through the three places chain ids live — the states'
-- llm_context_id, the store items' value.llm_context_id, and the
-- context-registry items' keys (namespace[3] = 'contexts').
--
-- Params (asyncpg $n, in order):
--   $1 thread_id  TEXT — ONE chain member's thread id
--
-- Deletes FIRST in the purge order: a request's chain_id is the node's
-- llm_context_id, resolvable only while the linking rows exist.
DELETE FROM llm.llm_requests WHERE chain_id IN (
SELECT s.llm_context_id FROM llm.deep_agent_states s
WHERE s.thread_id = $1 AND s.llm_context_id IS NOT NULL
UNION SELECT i.value ->> 'llm_context_id'
FROM llm.deep_agent_store_items i
WHERE i.namespace[1:2] = ARRAY['deep_agent', $1]::text[]
AND i.value ->> 'llm_context_id' IS NOT NULL
UNION SELECT i.key FROM llm.deep_agent_store_items i
WHERE i.namespace[1:3] = ARRAY['deep_agent', $1, 'contexts']::text[])
