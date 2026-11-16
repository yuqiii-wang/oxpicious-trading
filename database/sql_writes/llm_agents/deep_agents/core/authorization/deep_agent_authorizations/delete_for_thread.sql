-- SQL write action — llm.deep_agent_authorizations chain purge (the
-- temp-user row AND every node extension row of one run). Executed by
-- llm_agents/deep_agents/core/authorization/store.py
-- (AuthorizationStore.delete_for_thread) from the chain purge path
-- (planning store purge_super_thread / the UI thread delete) — the rows
-- are audit records, kept until the run itself goes.
--
-- Params (asyncpg $n, in order):
--   $1 super_thread_id TEXT — the run's CHAIN id (matches the thread
--                             temp-user row and every extension row)
DELETE FROM llm.deep_agent_authorizations
WHERE super_thread_id = $1
