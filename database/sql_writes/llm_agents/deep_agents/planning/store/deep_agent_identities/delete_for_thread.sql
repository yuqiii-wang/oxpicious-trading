-- SQL write action — llm.deep_agent_identities DELETE (one thread's
-- scope-identity link rows). Executed by llm_agents/deep_agents/planning/
-- store.py (PlanStore.purge_thread — the Python twin of the UI's thread
-- delete, data_viz/api/deep_agent_identities/delete_for_thread.sql).
--
-- Params (asyncpg $n, in order):
--   $1 thread_id  TEXT — ONE chain member's thread id
DELETE FROM llm.deep_agent_identities WHERE thread_id = $1
