-- llm_agents.deep_agents.server — the UI FORK action's transcript source
-- (2026-11-15): the fork chain's message-bearing request rows in order.
-- Each llm_requests row's request_payload carries the conversation AS
-- SENT for that turn — a chained row stores only its NEW messages
-- (_chain_incremental true, the shared prefix stored once by the first
-- row of the chain), so the FULL transcript is reconstructed host-side:
-- walk the rows in order (a non-incremental row REPLACES the list — a
-- full resend), accumulating up to the fork point's NEXT message row
-- (its delta = the fork point's assistant reply + tool results). A fork
-- on the chain's last message row continues from just before that reply.
-- args:
--   chain_id: value text — the state's llm_context_id (the request
--             chain); the fork POINT (an agent-step inference row's
--             request id) is located host-side among the returned rows.
SELECT r.request_id,
       (r.request_payload->>'_chain_incremental')::boolean AS incremental,
       r.request_payload->'messages'                        AS messages
  FROM llm.llm_requests r
 WHERE r.chain_id = {{chain_id}}
   AND r.request_payload->'messages' IS NOT NULL
 ORDER BY r.created_at, r.request_id
