-- ============================================================================
--  KG search-call stats — llm.kg_search_stats (2026-11-23)
--  kg entity (ns0 db_semantics_tables reads the payload below):
-- description: Every answered KG search call's own ledger row — the ranked
--   node names with their scores, what the seed and walk rungs found and
--   pruned, the replay and relevance-gate facts, and the latency.
-- tags: kg search stats, KG搜索统计, search ledger, 检索台账, search miss,
--   未命中, hot path replay, 热路径回放, ranking, 排名, latency, 延迟
--  ONE ROW PER kg_search CALL — the observability ledger of the search
--  lane. The chain tables record only SUCCESSFUL, non-empty live
--  traversals (llm.kg_queries = one row per chain edge, its repeatable
--  chains promoted into llm.kg_hotpaths), so a NO-MATCH search left no
--  trace anywhere and no score was ever persisted. This table records
--  EVERY answered call on every return path of
--  KnowledgeGraphService._search — the hot-path replay, the live search
--  (match or no match) and the gate-rejected result — with the ranked
--  node names + scores and the per-rung counters (what the seed / walk
--  rungs produced and what the relevance floors pruned).
--
--  Written fail-soft by llm_agents/kg/search_stats.py
--  (SearchStatsLog.record) AFTER the result text is built: a failed
--  write never breaks or alters a kg_search result. A pure side ledger
--  — nothing in the search path reads it, and the chain cache's
--  contracts are untouched: a miss is never logged as a chain and never
--  promotes (kg_queries / kg_hotpaths keep their own shapes).
--
--  No type / kind / state column by design: the outcome facts are
--  booleans, counters and nullable fields (this repo's closed-
--  vocabulary columns are SMALLINT label codes — none is needed here).
--  `namespace` is the kg_namespace code, the chain tables' convention.
--  What the MODEL saw as a no-match = n_nodes = 0 OR gate_rejected.
--
--  Runtime log, NOT seed data — no seed file under static/
--  knowledge_graphs/ ever touches it. No retention job yet (one small
--  row per agent tool call); it is not in the deep-agent runs prune.
--
--  Applied after kg_hotpaths.sql. Re-appliable as-is (IF NOT EXISTS
--  everywhere).
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE IF NOT EXISTS llm.kg_search_stats (
    stat_id           BIGINT      GENERATED ALWAYS AS IDENTITY,
    query             TEXT        NOT NULL,  -- the query text the tool received (stripped)
    namespace         SMALLINT    NOT NULL,  -- domain 'kg_namespace' — same codes as kg_entities (the searched subgraph)
    full_detail       BOOLEAN     NOT NULL,  -- the render tier: TRUE = 'full', FALSE = 'lean' (the default)
    hops              SMALLINT    NOT NULL,  -- the walk depth the call asked for (clamped 1..3)
    max_results       SMALLINT    NOT NULL,  -- the node budget the call asked for (clamped 4..16)
    forced_scope      BOOLEAN     NOT NULL,  -- a table / schema allowlist rode the search
    entity_hash       TEXT        NOT NULL,  -- the served graph's digest (KnowledgeGraph.entity_digest) — the chain cache's own validity key
    replayed          BOOLEAN     NOT NULL,  -- answered by a hot-path replay — no traversal ran
    replay_chain_id   TEXT,                  -- the replayed llm.kg_hotpaths chain_id (NULL = live)
    replay_exact      BOOLEAN,               -- TRUE = exact query text; FALSE = a token-equivalent / near-duplicate text; NULL = live
    replay_similarity REAL,                  -- the near-duplicate text similarity (NULL unless replay_exact is FALSE and the match was not token-equivalent)
    gate_read         BOOLEAN     NOT NULL,  -- a relevance judge ran on the ranked candidates
    gate_rejected     BOOLEAN     NOT NULL,  -- the judge rejected the result (the caller sends its no-match nudge)
    gate_accepted     BOOLEAN     NOT NULL,  -- the judge accepted a subset (the render was filtered to it)
    promoted          BOOLEAN     NOT NULL,  -- the fresh chain twin-matched and was promoted into llm.kg_hotpaths (the two-hit rule)
    n_nodes           SMALLINT    NOT NULL,  -- ranked nodes the engine returned (a replay: the chain's distinct member entities)
    n_tables          SMALLINT    NOT NULL,  -- non-template entities among them (tables, DB views, skills, label domains — not sql_views templates)
    n_edges           SMALLINT    NOT NULL,  -- packed edges (a replay: the chain's edges resolved against the live graph)
    n_tokens          SMALLINT    NOT NULL,  -- distinct query tokens the traversal matched with (0 for a replay)
    n_seeds           SMALLINT    NOT NULL,  -- entities the seed rung scored
    n_seed_kept       SMALLINT    NOT NULL,  -- seeds above the seed relevance floor
    n_seed_pruned     SMALLINT    NOT NULL,  -- seeds the floor dropped
    n_walk            SMALLINT    NOT NULL,  -- propagated (non-seed) nodes the rank rung scored
    n_walk_kept       SMALLINT    NOT NULL,  -- walk rows above the walk relevance floor
    n_walk_pruned     SMALLINT    NOT NULL,  -- walk rows the floor dropped
    n_fuzzy_tokens    SMALLINT    NOT NULL,  -- query tokens that matched only through the fuzzy (trigram) fallback
    seed_top          REAL,                  -- the raw top seed score (NULL = no seed scored)
    walk_top          REAL,                  -- the top blended walk score (NULL = no walk row)
    top_nodes         JSONB       NOT NULL,  -- the head of the ranking: [{name, kind, score}, …] — bounded; scores are 0 on a replay
    latency_ms        REAL        NOT NULL,  -- wall time of the whole answered call (graph lookup → result text)
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- A replay always names the chain it served.
    CONSTRAINT pk_kg_search_stats PRIMARY KEY (stat_id),
    CONSTRAINT ck_kg_search_stats_replay
        CHECK (replayed = (replay_chain_id IS NOT NULL))
);

-- The recency lanes ("the last N searches", per namespace).
CREATE INDEX IF NOT EXISTS ix_kg_search_stats_created
    ON llm.kg_search_stats (created_at);
CREATE INDEX IF NOT EXISTS ix_kg_search_stats_ns_created
    ON llm.kg_search_stats (namespace, created_at);

-- Per-query history.
CREATE INDEX IF NOT EXISTS ix_kg_search_stats_query
    ON llm.kg_search_stats (query);

-- The MISS lane: calls that ranked nothing — the asks whose vocabulary
-- the corpus lacks. Partial (tiny), and trigram-indexed so near-
-- duplicate misses cluster (similarity(query, $1) / query % $1) — the
-- same affordance the chain tables carry over their query texts.
CREATE INDEX IF NOT EXISTS ix_kg_search_stats_misses
    ON llm.kg_search_stats (created_at) WHERE n_nodes = 0;
CREATE INDEX IF NOT EXISTS ix_kg_search_stats_miss_query_gin
    ON llm.kg_search_stats USING gin (query gin_trgm_ops)
    WHERE n_nodes = 0;

COMMENT ON TABLE llm.kg_search_stats IS
    'KG search-call stats: ONE row per kg_search call on every return '
    'path (hot-path replay, live match, no-match, gate-rejected) — the '
    'ranked node names + scores, the per-rung counters (seeds / walk '
    'rows scored, kept and pruned by the relevance floors), the replay '
    'and gate facts, and the latency. Written fail-soft after the '
    'result text is built; nothing in the search path reads it and the '
    'chain tables (kg_queries / kg_hotpaths) keep their contracts. A '
    'no-match as the MODEL saw it = n_nodes = 0 OR gate_rejected. '
    'Runtime log only — never seeded.';

COMMENT ON COLUMN llm.kg_search_stats.query IS
    'The query text the kg_search tool received (stripped).';

COMMENT ON COLUMN llm.kg_search_stats.namespace IS
    'The searched subgraph — same codes as llm.kg_entities.namespace '
    '(domain kg_namespace): 0 DB semantics | 1 prompts | 2 data & '
    'labels. The kg_search default lane searches 0 and 2 together — '
    'one row per lane.';

COMMENT ON COLUMN llm.kg_search_stats.full_detail IS
    'The gradual-load render tier the call asked for: TRUE = full '
    '(every ranked node), FALSE = lean (the default — the head of the '
    'ranking). The tier never changes the traversal.';

COMMENT ON COLUMN llm.kg_search_stats.entity_hash IS
    'The served graph''s digest (KnowledgeGraph.entity_digest — md5 '
    'over the entities'' (entity_id, description) pairs), the same '
    'validity key the chain tables carry: rows logged under different '
    'hashes were served on different graph versions.';

COMMENT ON COLUMN llm.kg_search_stats.replayed IS
    'TRUE = the call was answered by a hot-path replay from '
    'llm.kg_hotpaths (no traversal ran — the rung counters are 0 and '
    'top_nodes carries the chain''s members at score 0).';

COMMENT ON COLUMN llm.kg_search_stats.replay_exact IS
    'How the replay''s hot path was matched: TRUE = the exact query '
    'text; FALSE = a different text judged equivalent (the same token '
    'multiset) or a near-duplicate (replay_similarity then carries the '
    'similarity); NULL = the call was live.';

COMMENT ON COLUMN llm.kg_search_stats.promoted IS
    'TRUE = this call''s fresh chain matched a chain already logged in '
    'llm.kg_queries edge-for-edge (the second identical production) and '
    'was promoted into llm.kg_hotpaths at hit_count 2. Always FALSE on '
    'a replay and on a miss.';

COMMENT ON COLUMN llm.kg_search_stats.n_tables IS
    'Non-template entities among the ranked nodes: tables, '
    'schema-qualified DB views, skills / plans and label domains — the '
    'slash-path sql_views template entities (damped read-side '
    'enrichment) do not count.';

COMMENT ON COLUMN llm.kg_search_stats.n_fuzzy_tokens IS
    'Query tokens with NO exact index bucket that seeded only through '
    'the fuzzy character-trigram fallback (a misspelling or a plural). '
    '0 on a replay.';

COMMENT ON COLUMN llm.kg_search_stats.top_nodes IS
    'The head of the ranking the engine returned — a JSON array of '
    '{name, kind, score}, at most 10 entries. On a replay the chain''s '
    'distinct members in chain order, every score 0 (a replay carries '
    'no scores).';

COMMENT ON COLUMN llm.kg_search_stats.latency_ms IS
    'Wall time of the answered call: graph lookup (cached), the replay '
    'probe or the traversal, the chain-cache writes, the gate read and '
    'the render — not this row''s own write.';
