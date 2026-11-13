-- ============================================================================
--  Knowledge-graph seed — namespace 0 (DB semantics): the whole-market
--  participation table — total exchange turnover (SS+SZ proxy), the raw
--  daily SSE share, and the advance/decline breadth of the stock
--  universe, one row per trading day. Conventions:
--  .agents/skills/kg-db-semantics. DDL: kg_entities.sql + kg_edges.sql.
--
--  Idempotent — entities upsert on uq_kg_entities_ns_type_name; edges
--  insert only when the (relation, sub_relation, entity_ids) triple is
--  absent.
-- ============================================================================
--  2026-11-09: the 'table' entities migrated to the corpus rule
--  db_semantics_tables; since 2026-11-10 the payloads live in the
--  tables' own database/sql/ DDL files — ONE TABLE = ONE FILE, payload
--  in the leading banner (formerly the parallel database/kg_tables/
--  corpus, filename = the schema-qualified name). This seed keeps
--  only what doesn't fit a rule: the edge INSERTs and the DB
--  view entities. The rule's ledger owns the migrated tables;
--  edges here still resolve them BY NAME.



INSERT INTO llm.kg_edges (namespace, relation, sub_relation, entity_ids)
SELECT 0, e.relation, e.sub_relation, ARRAY[a.entity_id, b.entity_id]::BIGINT[]
FROM (VALUES
    -- Participation sources (per analysis/market_participation):
    -- turnover + share from the exchange proxy totals, breadth from the
    -- whole-universe stock pct_change signs.
    ('analysis.market_participation', 'stats.exchange_trading_amt', 'computed_from', 'for_market_total'),
    ('analysis.market_participation', 'stats.stock_basic_stats',    'computed_from', 'for_breadth'),
    -- Same market-share concept as the per-security smoothed table
    -- (this one is the raw daily market-level share) — SYMMETRIC family
    ('analysis.market_participation', 'analysis.mov_ave_trading_amt', 'similar_to', 'about_market_share')
) AS e(subject, object, relation, sub_relation)
JOIN llm.kg_entities a ON a.namespace = 0 AND a.name = e.subject
JOIN llm.kg_entities b ON b.namespace = 0 AND b.name = e.object
WHERE NOT EXISTS (
    SELECT 1 FROM llm.kg_edges x
    WHERE x.namespace = 0
      AND x.relation  = e.relation
      AND x.sub_relation IS NOT DISTINCT FROM e.sub_relation
      AND x.entity_ids = ARRAY[a.entity_id, b.entity_id]::BIGINT[]
);
