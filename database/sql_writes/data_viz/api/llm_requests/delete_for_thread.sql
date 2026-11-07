-- SQL write action — llm.llm_requests DELETE (UI: Summary History, entry
-- delete). Executed by data_viz/api/services/ai-summary.service.ts
-- (deleteSummaryRun) through the pg primary client inside the per-member
-- transaction; convention of this tree:
--   database/sql_writes/<app>/<layer>/<table>/<action>.sql
--
-- Params (node-postgres $n, in order):
--   $1 thread_id       TEXT — ONE chain member's thread id
--
-- Deletes every request whose chain_id is linked to the member: the
-- node's llm_context_id (resolved from the states), the store's node
-- items' llm_context_id, or the contexts registry keys — while the
-- linking rows still exist (this runs FIRST in the chain delete).
DELETE FROM llm.llm_requests
 WHERE chain_id IN (
       SELECT s.llm_context_id FROM llm.deep_agent_states s
        WHERE s.thread_id = $1 AND s.llm_context_id IS NOT NULL
       UNION
       SELECT i.value ->> 'llm_context_id'
         FROM llm.deep_agent_store_items i
        WHERE i.namespace[1:2] = ARRAY['deep_agent', $1]::text[]
          AND i.value ->> 'llm_context_id' IS NOT NULL
       UNION
       SELECT i.key FROM llm.deep_agent_store_items i
        WHERE i.namespace[1:3] = ARRAY['deep_agent', $1, 'contexts']::text[])
