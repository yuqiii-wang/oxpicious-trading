-- SQL write action — llm.deep_agent_store_items DELETE (one item). Executed
-- by llm_agents/deep_agents/langgraph/state_store.py (AsyncPgStore._put)
-- when LangGraph issues a put with a None value: the item goes away.
--
-- Params (asyncpg $n, in order):
--   $1 namespace  TEXT[] — the item's full namespace path
--   $2 key        TEXT — the item's key
DELETE FROM llm.deep_agent_store_items
WHERE namespace = $1::text[] AND key = $2
