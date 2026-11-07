-- SQL write action — llm.kg_hotpaths UPDATE (count one replay). Executed
-- by llm_agents/kg/hotpaths.py (HotpathCache.lookup) in the same
-- transaction as the chain read: every replay of a hot path bumps every
-- row's hit_count, so hit_count tracks how often the path has been
-- served (the DDL CHECKs hit_count >= 2 — the promotion floor).
--
-- Params (asyncpg $n, in order):
--   $1 query      TEXT — the hot path's query text
--   $2 namespace  SMALLINT — the KG namespace (0 DB semantics |
--                 1 prompts)
UPDATE llm.kg_hotpaths SET hit_count = hit_count + 1
WHERE query = $1 AND namespace = $2
