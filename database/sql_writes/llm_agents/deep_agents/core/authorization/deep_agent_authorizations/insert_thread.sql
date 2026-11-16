-- SQL write action — llm.deep_agent_authorizations THREAD TEMP-USER
-- upsert (the run's init scope row: node_id NULL). Executed by
-- llm_agents/deep_agents/core/authorization/store.py
-- (AuthorizationStore.ensure_thread_grant) at RUN START from the
-- request's init scope (UI forced tables / schemas; daemon runs default
-- to the standing schema allowlist). Idempotent: a re-run refreshes the
-- permission (DO UPDATE) without touching created_at.
--
-- Params (asyncpg $n, in order):
--   $1 super_thread_id TEXT — the run's CHAIN id (the root plan
--                             thread's own id)
--   $2 permission        JSONB — the effective init scope object:
--                             {tables, schemas, added_tables, focus,
--                              source: "ui_init"}
INSERT INTO llm.deep_agent_authorizations
    (super_thread_id, node_id, permission)
VALUES ($1, NULL, $2::jsonb)
ON CONFLICT (super_thread_id) WHERE node_id IS NULL
DO UPDATE SET permission = EXCLUDED.permission
