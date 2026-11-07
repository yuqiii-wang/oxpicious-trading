-- SQL write action — llm.kg_hotpaths DELETE (one (query, namespace)
-- key's previous rows). Executed by llm_agents/kg/hotpaths.py
-- (HotpathCache.promote) inside the promotion transaction: a
-- re-promotion under a new entity_hash replaces the chain (and so
-- resets its hit_count).
--
-- Params (asyncpg $n, in order):
--   $1 query      TEXT — the hot path's query text
--   $2 namespace  SMALLINT — the KG namespace (0 DB semantics |
--                 1 prompts)
DELETE FROM llm.kg_hotpaths
WHERE query = $1 AND namespace = $2
