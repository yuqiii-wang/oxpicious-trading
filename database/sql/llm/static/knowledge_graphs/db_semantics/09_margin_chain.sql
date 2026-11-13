-- ============================================================================
--  Knowledge-graph seed — namespace 0 (DB semantics): the margin (融资融券)
--  chain — per-index / per-industry margin series, the trend-detection
--  input, the trend-episode summary, and the exchanges' official market-wide
--  totals. Conventions: .agents/skills/kg-db-semantics. DDL:
--  kg_entities.sql + kg_edges.sql.
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
    -- Per-index / per-industry margin series aggregate the per-security
    -- margin balances (per their DDL comments)
    ('analysis.margin_index_series',   'stats.stock_margin',       'computed_from', 'for_margin_balance'),
    ('analysis.margin_index_series',   'stats.etf_liquidity_margin','computed_from', 'for_margin_balance'),
    ('analysis.margin_index_series',   'stats.sec_classification', 'computed_from', 'for_member_weights'),
    ('analysis.margin_industry_stats', 'stats.stock_margin',       'computed_from', 'for_margin_balance'),
    ('analysis.margin_industry_stats', 'stats.etf_liquidity_margin','computed_from', 'for_margin_balance'),
    ('analysis.margin_industry_stats', 'stats.sec_classification', 'computed_from', 'for_industry_map'),
    -- Trend detection chain
    ('analysis.margin_tech_stats', 'analysis.margin_index_series',   'computed_from', 'for_margin_series'),
    ('analysis.margin_tech_stats', 'analysis.margin_industry_stats', 'computed_from', 'for_margin_series'),
    ('analysis.margin_changes',    'analysis.margin_tech_stats',     'computed_from', 'for_trend_slope')
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
