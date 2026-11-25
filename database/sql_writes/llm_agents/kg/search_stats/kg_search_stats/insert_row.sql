-- SQL write action — llm.kg_search_stats INSERT (one kg_search call's
-- stats row). Executed by llm_agents/kg/search_stats.py
-- (SearchStatsLog.record) via asyncpg execute AFTER the result text is
-- built — fail-soft: a failed write never reaches the search result.
-- One row per call on every return path (hot-path replay, live match,
-- no-match, gate-rejected).
--
-- Params (asyncpg $n, in order — KgSearchStatRow's field order):
--   $1  query              TEXT — the query text the tool received (stripped)
--   $2  namespace          SMALLINT — the KG namespace (0 | 1 | 2)
--   $3  full_detail        BOOLEAN — the render tier was 'full'
--   $4  hops               SMALLINT — the walk depth asked (1..3)
--   $5  max_results        SMALLINT — the node budget asked (4..16)
--   $6  forced_scope       BOOLEAN — a table / schema allowlist rode the search
--   $7  entity_hash        TEXT — the served graph's digest
--   $8  replayed           BOOLEAN — a hot-path replay answered
--   $9  replay_chain_id    TEXT — the replayed chain (NULL = live)
--   $10 replay_exact       BOOLEAN — exact-text replay (NULL = live)
--   $11 replay_similarity  REAL — the near-duplicate similarity (NULL otherwise)
--   $12 gate_read          BOOLEAN — a relevance judge ran
--   $13 gate_rejected      BOOLEAN — the judge rejected the result
--   $14 gate_accepted      BOOLEAN — the judge accepted a subset
--   $15 promoted           BOOLEAN — the fresh chain was promoted
--   $16 n_nodes            SMALLINT — ranked nodes
--   $17 n_tables           SMALLINT — non-template entities among them
--   $18 n_edges            SMALLINT — packed edges
--   $19 n_tokens           SMALLINT — distinct query tokens
--   $20 n_seeds            SMALLINT — entities the seed rung scored
--   $21 n_seed_kept        SMALLINT — seeds above the seed floor
--   $22 n_seed_pruned      SMALLINT — seeds the floor dropped
--   $23 n_walk             SMALLINT — propagated nodes scored
--   $24 n_walk_kept        SMALLINT — walk rows above the walk floor
--   $25 n_walk_pruned      SMALLINT — walk rows the floor dropped
--   $26 n_fuzzy_tokens     SMALLINT — tokens seeded only by the fuzzy fallback
--   $27 seed_top           REAL — raw top seed score (NULL = no seed)
--   $28 walk_top           REAL — top blended walk score (NULL = no walk row)
--   $29 top_nodes          JSONB — [{name, kind, score}, …] (≤ 10)
--   $30 latency_ms         REAL — wall time of the answered call
INSERT INTO llm.kg_search_stats
    (query, namespace, full_detail, hops, max_results, forced_scope,
     entity_hash, replayed, replay_chain_id, replay_exact,
     replay_similarity, gate_read, gate_rejected, gate_accepted,
     promoted, n_nodes, n_tables, n_edges, n_tokens, n_seeds,
     n_seed_kept, n_seed_pruned, n_walk, n_walk_kept, n_walk_pruned,
     n_fuzzy_tokens, seed_top, walk_top, top_nodes, latency_ms)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14,
        $15, $16, $17, $18, $19, $20, $21, $22, $23, $24, $25, $26,
        $27, $28, $29::jsonb, $30)
