-- llm_agents.deep_agents.server — one runner-lane node's OWN request
-- chain (2026-11-15): the node item (llm.deep_agent_store_items key
-- node:<name> in the OWNING segment's namespace) carries the node
-- conversation's llm_context_id — the fork point's chain for the UI
-- fork action on a state-less seat (the seat has no state row whose
-- llm_context_id the state-keyed fork route could read) — plus the
-- item's status and error, which the fork action records on the
-- pre-fork attempt's old-chain stub (the failed node on the side
-- branch).
-- args:
--   thread_id: value text — the OWNING segment thread (the service
--              resolves the canvas node id's s<k>: prefix)
--   node:      value text — the runner-lane node name (e.g. output)
SELECT value->>'llm_context_id' AS chain_id,
       value->>'status'         AS seat_status,
       value->>'error'          AS seat_error
  FROM llm.deep_agent_store_items
 WHERE namespace = ARRAY['deep_agent', {{thread_id}}]
   AND key = 'node:' || {{node}}
   AND value->>'llm_context_id' IS NOT NULL
 LIMIT 1
