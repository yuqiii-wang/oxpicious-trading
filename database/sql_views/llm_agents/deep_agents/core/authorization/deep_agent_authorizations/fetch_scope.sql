-- Effective-scope rows for ONE (run chain, node): the node's OWN
-- extension row (when it has one) and the run's thread temp-user row —
-- the resolver picks the node row first, the thread row is the fallback
-- every un-rowed node shares. A NULL node_id fetches only the thread
-- row. Served read for llm_agents/deep_agents/core/authorization
-- (AuthorizationStore.fetch_effective).
-- args:
--   super_thread_id: value text — the run's CHAIN id
--   node_id: value bigint|None — the node's state id; NULL = thread row only
SELECT auth_id,
       super_thread_id,
       node_id,
       permission,
       created_at
FROM llm.deep_agent_authorizations
WHERE super_thread_id = {{super_thread_id}}::text
  AND (node_id IS NULL
       OR ({{node_id}}::bigint IS NOT NULL
           AND node_id = {{node_id}}::bigint))
