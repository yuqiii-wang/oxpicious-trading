-- SQL write action — llm.deep_agent_authorizations NODE EXTENSION
-- upsert (one explicitly study-focused node's widened scope). Executed
-- by llm_agents/deep_agents/core/authorization/store.py
-- (AuthorizationStore.upsert_node_grant) from SpawnHooks.input_hook when
-- a spawn carries study_focus: the permission is the PARENT scope UNION
-- the KG-located tables for that focus (extend, never narrow),
-- materialized at write time so resolution stays one row read.
-- Idempotent: a re-grant refreshes the permission (DO UPDATE).
--
-- Params (asyncpg $n, in order):
--   $1 super_thread_id TEXT — the run's CHAIN id
--   $2 node_id          BIGINT — the node's llm.deep_agent_states.state_id
--   $3 permission        JSONB — the effective scope object:
--                             {tables, schemas, added_tables, focus,
--                              source: "kg_extension"}
INSERT INTO llm.deep_agent_authorizations
    (super_thread_id, node_id, permission)
VALUES ($1, $2::bigint, $3::jsonb)
ON CONFLICT (super_thread_id, node_id) WHERE node_id IS NOT NULL
DO UPDATE SET permission = EXCLUDED.permission
