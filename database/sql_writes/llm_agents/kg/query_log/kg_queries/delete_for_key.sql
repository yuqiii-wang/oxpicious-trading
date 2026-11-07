-- SQL write action — llm.kg_queries DELETE (one (query, namespace) key's
-- previous chain). Executed by llm_agents/kg/query_log.py
-- (QueryLog.log_chain) inside the relog transaction: at most one live
-- chain per key, so a relog under a new entity_hash drops the stale one.
--
-- Params (asyncpg $n, in order):
--   $1 query      TEXT — the search's query text
--   $2 namespace  SMALLINT — the KG namespace (0 DB semantics |
--                 1 prompts)
DELETE FROM llm.kg_queries
WHERE query = $1 AND namespace = $2
