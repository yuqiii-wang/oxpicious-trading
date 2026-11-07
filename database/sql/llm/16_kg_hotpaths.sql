-- ============================================================================
--  KG search HOT PATHS — llm.kg_hotpaths (2026-11-06)
--  The PROMOTED replay layer of the kg_search chain cache: same row
--  shape as llm.kg_queries (one row per EDGE of one chain — DDL
--  database/sql/llm/14_kg_queries.sql) plus hit_count. The deep
--  agents' kg_search (llm_agents/deep_agents/tools/kg_tools/) checks
--  THIS table FIRST by query EXACT MATCH (the (query, namespace)
--  index) over an unchanged graph (entity_hash): a hit REPLAYS the
--  logged chain without re-running the traversal and bumps hit_count
--  — llm.kg_queries itself no longer serves replays, it is the LOG /
--  two-hit EVIDENCE table (see below).
--
--  A chain lands here only after its result has proven REPEATABLE —
--  the TWO-HIT rule. On a hotpaths miss the live traversal runs and
--  its fresh packed chain is compared against EVERY chain logged in
--  llm.kg_queries under the same (namespace, entity_hash) — ANY
--  query text, matched edge-for-edge in depth order, BEFORE the
--  fresh chain is relogged (a just-logged chain would trivially
--  match itself). An identical twin means the chain has now been
--  produced TWICE (the logged hit + the fresh hit): the query is
--  PROMOTED — the writer's DELETE + INSERT replaces the query's
--  previous hotpath rows with the chain at hit_count = 2 (the two
--  hits that earned the promotion). Every later replay of the hot
--  path bumps hit_count, so the counter tracks how often the path
--  has been served; the promoting search itself is answered by the
--  live render (the replay lane starts with the NEXT ask).
--
--  hit_count carries the promotion invariant: rows are inserted at 2
--  and only ever incremented — CHECK (hit_count >= 2) keeps a
--  sub-promotion row from ever existing. A graph edit that flips
--  entity_digest turns this table's rows stale (lookup miss): the
--  live search + twin-compare + relog cycle then re-derives and
--  re-promotes the chain under the new digest, replacing the stale
--  rows for the queries that still repeat.
--
--  Indexes mirror kg_queries: a (query, namespace) btree serves the
--  exact-match lookup and the writer's replace-DELETE, and a trigram
--  GIN (pg_trgm) serves similarity / LIKE probing over the HOT query
--  texts (the same near-duplicate detection as kg_queries, over the
--  promoted subset). pg_trgm is trusted (PG13+), already required by
--  14_kg_queries.sql.
--
--  Applied after 14_kg_queries.sql. Re-appliable as-is
--  (IF NOT EXISTS everywhere). Runtime cache, NOT seed data — no
--  seed file under static/knowledge_graphs/ ever touches it.
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE IF NOT EXISTS llm.kg_hotpaths (
    query       TEXT        NOT NULL,  -- exact query text the tool received (stripped) — NOT a PK column; indexed for lookup (btree (query, namespace) + trigram GIN)
    namespace   SMALLINT    NOT NULL,  -- domain 'kg_namespace' — same codes as kg_entities/kg_edges (the searched subgraph); PK column
    chain_id    TEXT        NOT NULL,  -- uuid hex; ONE per promotion (the chain it replays), same shape as llm_requests.chain_id
    edge_id     BIGINT      NOT NULL,  -- llm.kg_edges.edge_id — one row per chain edge; soft reference (no FK), replays skip ids the live graph no longer has
    depth       INTEGER     NOT NULL,  -- the edge's 0-based ORDINAL in the chain (position among its edges — NOT the search hops)
    entity_hash TEXT        NOT NULL,  -- md5 over the namespace's (entity_id, description) — hot-path validity key (same digest as llm.kg_queries.entity_hash)
    hit_count   INTEGER     NOT NULL DEFAULT 2,  -- times this (query, chain) has been served: seeded 2 at promotion (the two identical productions), +1 per replay
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT pk_kg_hotpaths PRIMARY KEY (namespace, chain_id, edge_id),
    CONSTRAINT ck_kg_hotpaths_hits CHECK (hit_count >= 2)
);

-- The exact-match replay lookup (WHERE query = $1 AND namespace = $2):
-- one live chain per key, so this pair is fully selective.
CREATE INDEX IF NOT EXISTS ix_kg_hotpaths_query_ns
    ON llm.kg_hotpaths (query, namespace);

-- Similarity / LIKE probing over the hot query texts; the
-- exact-match lookup rides ix_kg_hotpaths_query_ns above.
CREATE INDEX IF NOT EXISTS ix_kg_hotpaths_query_gin
    ON llm.kg_hotpaths USING gin (query gin_trgm_ops);

COMMENT ON TABLE llm.kg_hotpaths IS
    'KG search hot paths: the PROMOTED replay layer of the chain '
    'cache — one row per EDGE of one chain (same shape as '
    'llm.kg_queries plus hit_count). kg_search replays ONLY from '
    'here: a query lands in this table after its result chain was '
    'produced identically TWICE (a chain logged in kg_queries + a '
    'fresh traversal matching it edge-for-edge under the same '
    'entity_hash, any query text) — the writer promotes it with '
    'hit_count = 2 and every replay bumps the counter. A lookup '
    'miss (never logged twice, or a stale entity_hash after a graph '
    'edit) runs the live traversal, which relogs to kg_queries and '
    're-promotes when the twin match re-proves. Written fail-soft '
    'by llm_agents/deep_agents/tools/kg_tools/ hotpaths. Runtime '
    'cache only — never seeded.';

COMMENT ON COLUMN llm.kg_hotpaths.query IS
    'The exact query text the kg_search tool received (stripped). NOT '
    'a PK column (the PK keys the chain''s own rows) but indexed for '
    'lookup both ways: the (query, namespace) btree for the '
    'exact-match replay lookup and the writer''s replace-DELETE, and '
    'a trigram GIN (pg_trgm) for similarity/LIKE probing over the '
    'hot query texts.';

COMMENT ON COLUMN llm.kg_hotpaths.namespace IS
    'The searched subgraph — same codes as llm.kg_entities.namespace '
    '(domain kg_namespace, not yet registered): 0 DB semantics | 1 '
    'prompts. PK column: the same query text in two namespaces is two '
    'chains (different chain_ids).';

COMMENT ON COLUMN llm.kg_hotpaths.chain_id IS
    'uuid hex minted per PROMOTION — the id of the chain this hot '
    'path replays (same text shape as llm_requests.chain_id). All '
    'rows of one hot chain share it; a re-promotion of the same '
    '(query, namespace) (e.g. under a new entity_hash) replaces the '
    'old rows, so at most one live hot chain exists per key.';

COMMENT ON COLUMN llm.kg_hotpaths.edge_id IS
    'llm.kg_edges.edge_id of one edge in the chain''s packed result. '
    'Soft reference (no FK, the KG''s own member-existence '
    'convention): a replay resolves ids against the live graph and '
    'skips any that no longer exist — an empty replay falls back to '
    'a fresh search.';

COMMENT ON COLUMN llm.kg_hotpaths.depth IS
    'The edge''s 0-based ORDINAL in its chain — depth reflects the '
    'edge''s position among the chain''s edges, and ORDER BY depth '
    'restores the packing order (internal edges first, then the '
    'outward join-hint tier) so a replay renders the order the live '
    'search would have. NOT the search''s hops parameter: the chain '
    'is keyed by exact query text, hops/max_results are not identity.';

COMMENT ON COLUMN llm.kg_hotpaths.entity_hash IS
    'md5 over the namespace''s entities'' (entity_id, description) '
    'pairs sorted by id (KnowledgeGraph.entity_digest at graph load) '
    '— the hot-path validity key, the same digest rule as '
    'llm.kg_queries.entity_hash: a graph edit flips it, the lookup '
    'misses, and the live cycle re-derives and re-promotes the chain '
    'under the new digest.';

COMMENT ON COLUMN llm.kg_hotpaths.hit_count IS
    'How many times this (query, chain) has been SERVED. Seeded at 2 '
    'by the promotion (the two identical productions: the chain '
    'logged in kg_queries + the fresh traversal that matched it) and '
    'bumped +1 by every replay of the hot path. CHECK (>= 2) keeps '
    'the promotion invariant: a row below 2 has never been a hot '
    'path. The counter resets to 2 on re-promotion under a new '
    'entity_hash — the old graph''s popularity does not carry over.';
