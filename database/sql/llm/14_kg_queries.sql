-- ============================================================================
--  KG search query log / chain cache — llm.kg_queries (2026-11-05)
--  One row per EDGE of one successful kg_search result CHAIN. The deep
--  agents' kg_search (llm_agents/deep_agents/tools/kg_tools/) logs every
--  non-empty traversal result here. Since 2026-11-06 this table is the
--  LOG / two-hit EVIDENCE layer of the chain cache, NOT the replay
--  source: replays come from the PROMOTED llm.kg_hotpaths (DDL
--  16_kg_hotpaths.sql), and a fresh traversal's packed chain is
--  compared against EVERY chain logged here under the same
--  (namespace, entity_hash) — any query text, edge-for-edge in depth
--  order — BEFORE the fresh chain is relogged; an identical twin (the
--  chain produced twice) promotes the query into kg_hotpaths.
--
--  A successful search = ONE chain_id + MULTIPLE edge rows. depth is
--  each edge's 0-based ORDINAL in the chain — depth REFLECTS the
--  edge's position, and ORDER BY depth restores the packing order
--  (edges fully inside the chosen set first, then the outward
--  join-hint tier). The hops/max_results the search ran with are NOT
--  identity: the chain is keyed by exact query text, and ONE chain is
--  live per (query, namespace) — the writer's DELETE + INSERT replaces
--  the query's previous chain in one transaction.
--
--  entity_hash is the validity key: md5 over the namespace's entities'
--  (entity_id, description) pairs sorted by id (computed by
--  KnowledgeGraph.entity_digest at load) — the hash is ABOUT ENTITY
--  DESCRIPTIONS: edit one (or add/remove an entity) and every cached
--  chain of that namespace invalidates; renames and tag/edge edits
--  deliberately do not (replays resolve entities and edges from the
--  live graph, so renames surface in replays automatically).
--
--  query is deliberately NOT a PK column (the PK keys the chain's own
--  rows: namespace, chain_id, edge_id) but is indexed for lookup BOTH
--  ways: a (query, namespace) btree serves the writer's replace-DELETE
--  (and any per-key probing), and a trigram GIN (pg_trgm) serves
--  similarity / LIKE probing over the logged query texts
--  (near-duplicate detection, similarity(query, $1) / query % $1).
--  pg_trgm is trusted (PG13+) — creating it needs a role with CREATE
--  on the database.
--
--  Applied after 13_kg_hypergraph.sql. Re-appliable as-is
--  (IF NOT EXISTS everywhere). Runtime log, NOT seed data — no seed
--  file under static/knowledge_graphs/ ever touches it.
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE IF NOT EXISTS llm.kg_queries (
    query       TEXT        NOT NULL,  -- exact query text the tool received (stripped) — NOT a PK column; indexed for lookup (btree (query, namespace) + trigram GIN)
    namespace   SMALLINT    NOT NULL,  -- domain 'kg_namespace' — same codes as kg_entities/kg_edges (the searched subgraph); PK column
    chain_id    TEXT        NOT NULL,  -- uuid hex; ONE per successful search (its result chain), same shape as llm_requests.chain_id
    edge_id     BIGINT      NOT NULL,  -- llm.kg_edges.edge_id — one row per chain edge; soft reference (no FK), replays skip ids the live graph no longer has
    depth       INTEGER     NOT NULL,  -- the edge's 0-based ORDINAL in the chain (position among its edges — NOT the search hops)
    entity_hash TEXT        NOT NULL,  -- md5 over the namespace's (entity_id, description) — chain-cache validity key
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT pk_kg_queries PRIMARY KEY (namespace, chain_id, edge_id)
);

-- The writer's replace-DELETE and per-key probing (WHERE query = $1
-- AND namespace = $2): one live chain per key, so this pair is fully
-- selective.
CREATE INDEX IF NOT EXISTS ix_kg_queries_query_ns
    ON llm.kg_queries (query, namespace);

-- Similarity / LIKE probing over the logged query texts; the
-- replace-DELETE rides ix_kg_queries_query_ns above.
CREATE INDEX IF NOT EXISTS ix_kg_queries_query_gin
    ON llm.kg_queries USING gin (query gin_trgm_ops);

COMMENT ON TABLE llm.kg_queries IS
    'KG search query log / chain cache: one row per EDGE of one '
    'successful kg_search result chain (ONE chain_id, MULTIPLE edge '
    'rows, one live chain per (query, namespace)). Written fail-soft '
    'by llm_agents/deep_agents/tools/kg_tools/ query_log after every '
    'non-empty traversal. Since 2026-11-06 the LOG / two-hit EVIDENCE '
    'layer of the chain cache: kg_search replays from the promoted '
    'llm.kg_hotpaths, and a fresh chain matching a chain logged here '
    'edge-for-edge under the same (namespace, entity_hash) — any '
    'query text — is the second identical production that promotes '
    'the query into kg_hotpaths. Runtime log only — never seeded.';

COMMENT ON COLUMN llm.kg_queries.query IS
    'The exact query text the kg_search tool received (stripped). NOT '
    'a PK column (the PK keys the chain''s own rows) but indexed for '
    'lookup both ways: the (query, namespace) btree for the writer''s '
    'replace-DELETE and per-key probing, and a trigram GIN (pg_trgm) '
    'for similarity/LIKE probing over logged queries.';

COMMENT ON COLUMN llm.kg_queries.namespace IS
    'The searched subgraph — same codes as llm.kg_entities.namespace '
    '(domain kg_namespace, not yet registered): 0 DB semantics | 1 '
    'prompts. PK column: the same query text in two namespaces is two '
    'chains (different chain_ids).';

COMMENT ON COLUMN llm.kg_queries.chain_id IS
    'uuid hex minted per successful search — the id of its result '
    'CHAIN (same text shape as llm_requests.chain_id). All rows of one '
    'chain share it; a relog of the same (query, namespace) replaces '
    'the old chain''s rows, so at most one live chain exists per key.';

COMMENT ON COLUMN llm.kg_queries.edge_id IS
    'llm.kg_edges.edge_id of one edge in the chain''s packed result. '
    'Soft reference (no FK, the KG''s own member-existence convention): '
    'a replay resolves ids against the live graph and skips any that '
    'no longer exist — an empty replay falls back to a fresh search.';

COMMENT ON COLUMN llm.kg_queries.depth IS
    'The edge''s 0-based ORDINAL in its chain — depth reflects the '
    'edge''s position among the chain''s edges, and ORDER BY depth '
    'restores the packing order (internal edges first, then the '
    'outward join-hint tier) so a replay renders the order the live '
    'search would have. NOT the search''s hops parameter: the chain is '
    'keyed by exact query text, hops/max_results are not identity.';

COMMENT ON COLUMN llm.kg_queries.entity_hash IS
    'md5 over the namespace''s entities'' (entity_id, description) '
    'pairs sorted by id (KnowledgeGraph.entity_digest at graph load) — '
    'the chain-cache validity key, ABOUT ENTITY DESCRIPTIONS: an edit '
    'or add/remove invalidates every cached chain of the namespace; '
    'renames and tag/edge edits do not (replays read the live graph).';
