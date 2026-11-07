-- SQL write action — llm.deep_agent_store_items UPSERT (one item's
-- value). Executed by llm_agents/deep_agents/langgraph/state_store.py
-- (AsyncPgStore._put) for every LangGraph store put: node state items
-- (the UI's live graph reads them by poll + WS) and the LLM-context
-- registry items. A put REPLACES the whole item value (the writer
-- re-stamps fields like the checkpoint id accordingly).
--
-- Params (asyncpg $n, in order):
--   $1 namespace  TEXT[] — the item's full namespace path
--   $2 key        TEXT — the item's key
--   $3 value      JSONB — the item document (host-serialized, no
--                 transcripts — the context hygiene whitelist)
INSERT INTO llm.deep_agent_store_items (namespace, key, value)
VALUES ($1::text[], $2, $3::jsonb)
ON CONFLICT (namespace, key) DO UPDATE SET
value = EXCLUDED.value, updated_at = now()
