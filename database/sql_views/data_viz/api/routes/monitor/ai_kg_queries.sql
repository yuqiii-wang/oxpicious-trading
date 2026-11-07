-- AI knowledge-graph queries — the deep agents' kg_search chain cache /
-- log (llm.kg_queries), one row per logged CHAIN: the exact query text,
-- its namespace, the chain id, the edge count (depth spans 0..n-1 by
-- construction — min/max depth sent as-is), the entity-description hash
-- the chain is valid under, and when it was logged. The table is a
-- runtime cache written per search (one chain per (query, namespace)),
-- so this is recent-first with a limit — newest chains and their
-- staleness hash at a glance. Serves GET /api/monitor/ai/kg-queries.
-- args:
--   limit: value int — max chains returned (clamped 1-500 route-side)
SELECT query,
       namespace,
       chain_id,
       COUNT(*)::int       AS edge_count,
       MIN(depth)::int     AS min_depth,
       MAX(depth)::int     AS max_depth,
       MIN(entity_hash)    AS entity_hash,
       MIN(created_at)     AS created_at
  FROM llm.kg_queries
 GROUP BY query, namespace, chain_id
 ORDER BY created_at DESC
 LIMIT {{limit}}
