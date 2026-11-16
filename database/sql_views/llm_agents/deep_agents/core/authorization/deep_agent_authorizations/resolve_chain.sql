-- A segment thread's CHAIN id (the authorization key): the states'
-- super_thread_id when the thread belongs to a chain, the thread's own
-- id for legacy single-thread runs (the COALESCE(super_thread_id,
-- thread_id) chain-read contract). Served read for
-- llm_agents/deep_agents/core/authorization
-- (AuthorizationStore.resolve_chain_id).
-- args:
--   thread_id: value text — any segment thread id of the run
SELECT COALESCE(s.super_thread_id, s.thread_id) AS chain_id
FROM llm.deep_agent_states s
WHERE s.thread_id = {{thread_id}}::text
LIMIT 1
