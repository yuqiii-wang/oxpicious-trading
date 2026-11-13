-- SQL write action — llm.deep_agent_aux_steps DELETE (one thread's
-- auxiliary steps). Executed by
-- llm_agents/deep_agents/planning/store/chain.py (PlanStore.purge_thread
-- — the Python twin of the UI's thread delete, the same order
-- 09_deep_agent_runs_prune.sql follows). The resolution mirrors
-- llm_requests/delete_chains_for_thread.sql: an aux step's chain ids
-- live in the same three places (the states' llm_context_id, the store
-- items' value.llm_context_id, the context-registry items' keys), and
-- chain-stamped rows of the whole run die with the ROOT member (the
-- root thread's id IS the super_thread_id).
--
-- Params (asyncpg $n, in order):
--   $1 thread_id  TEXT — ONE chain member's thread id
DELETE FROM llm.deep_agent_aux_steps
 WHERE thread_id = $1
    OR super_thread_id = $1
    OR llm_context_id IN (
        SELECT s.llm_context_id FROM llm.deep_agent_states s
         WHERE s.thread_id = $1 AND s.llm_context_id IS NOT NULL
        UNION SELECT i.value ->> 'llm_context_id'
          FROM llm.deep_agent_store_items i
         WHERE i.namespace[1:2] = ARRAY['deep_agent', $1]::text[]
           AND i.value ->> 'llm_context_id' IS NOT NULL
        UNION SELECT i.key
          FROM llm.deep_agent_store_items i
         WHERE i.namespace[1:3] = ARRAY['deep_agent', $1, 'contexts']::text[])
