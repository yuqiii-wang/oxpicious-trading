-- SQL write action — llm.kg_hotpaths INSERT (one edge row of a PROMOTED
-- chain). Executed by llm_agents/kg/hotpaths.py (HotpathCache.promote)
-- via asyncpg executemany — one row per edge under ONE fresh uuid-hex
-- chain_id, hit_count = 2 (the two identical productions — the logged
-- kg_queries hit + the fresh traversal that matched it — that earned
-- the promotion), each row's depth its 0-based ordinal among the
-- chain's edges.
--
-- Params (asyncpg $n, per row, in order):
--   $1 query        TEXT — the hot path's query text
--   $2 chain_id     TEXT — the fresh uuid-hex chain id
--   $3 edge_id      BIGINT — the packed edge's id
--   $4 depth        INTEGER — the edge's 0-based ordinal in the chain
--   $5 namespace    SMALLINT — the KG namespace (0 | 1)
--   $6 entity_hash  TEXT — the live-graph digest the chain is valid
--                   under
--   $7 hit_count    INTEGER — the promotion seed
--                   (hotpaths.PROMOTION_HIT_COUNT = 2)
INSERT INTO llm.kg_hotpaths
    (query, chain_id, edge_id, depth, namespace, entity_hash, hit_count)
VALUES ($1, $2, $3, $4, $5, $6, $7)
