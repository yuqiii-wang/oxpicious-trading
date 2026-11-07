-- SQL write action — llm.kg_queries INSERT (one edge row of a logged
-- chain). Executed by llm_agents/kg/query_log.py (QueryLog.log_chain)
-- via asyncpg executemany — one row per edge under ONE fresh uuid-hex
-- chain_id, each row's depth its 0-based ordinal among the chain's
-- edges (the packing order the render used). The LOG / TWO-HIT EVIDENCE
-- half of the chain cache: a chain produced TWICE (matched edge-for-edge
-- by find_identical_chain) promotes the query into llm.kg_hotpaths.
--
-- Params (asyncpg $n, per row, in order):
--   $1 query        TEXT — the search's query text
--   $2 chain_id     TEXT — the fresh uuid-hex chain id
--   $3 edge_id      BIGINT — the packed edge's id
--   $4 depth        INTEGER — the edge's 0-based ordinal in the chain
--   $5 namespace    SMALLINT — the KG namespace (0 | 1)
--   $6 entity_hash  TEXT — the live-graph digest
--                   (KnowledgeGraph.entity_digest, md5 over the
--                   entities' descriptions — an edit/add/remove
--                   invalidates)
INSERT INTO llm.kg_queries
    (query, chain_id, edge_id, depth, namespace, entity_hash)
VALUES ($1, $2, $3, $4, $5, $6)
