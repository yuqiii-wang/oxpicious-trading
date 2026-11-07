-- SQL write action — llm.deep_agent_store_items DELETE (one thread's
-- store items, both namespaces). Executed by
-- llm_agents/deep_agents/langgraph/state_store.py (delete_thread_items)
-- — the purge helper PlanStore.purge_thread calls alongside its other
-- deletes; the Python twin of the UI's
-- data_viz/api/deep_agent_store_items/delete_for_thread.sql.
--
-- Params (asyncpg $n, in order):
--   $1 ns_prefix  TEXT[] — the namespace prefix sliced to its first two
--                 labels ('deep_agent', thread_id) — namespace[1:2]
DELETE FROM llm.deep_agent_store_items
WHERE namespace[1:2] = $1::text[]
