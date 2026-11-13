-- ============================================================================
--  Knowledge-graph seed — namespace 0 (DB semantics): the contract-level
--  options chain in stats (identity / terms / strike / settlement / greeks /
--  volume+oi / per-underlying aggregate). Conventions:
--  .agents/skills/kg-db-semantics. DDL: kg_entities.sql + kg_edges.sql.
--
--  Idempotent — entities upsert on uq_kg_entities_ns_type_name; edges
--  insert only when the (relation, sub_relation, entity_ids) triple is
--  absent. Also carries stats.v_options_quote's edges (the view entity is
--  seeded in 03, its member entities here).
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
    -- Sub-tables FK options_identity on (contract_code, date)
    ('stats.options_terms',      'stats.options_identity', 'anchored_by', 'for_code_date'),
    ('stats.options_strike',     'stats.options_identity', 'anchored_by', 'for_code_date'),
    ('stats.options_settlement', 'stats.options_identity', 'anchored_by', 'for_code_date'),
    ('stats.options_greeks',     'stats.options_identity', 'anchored_by', 'for_code_date'),
    ('stats.options_volume_oi',  'stats.options_identity', 'anchored_by', 'for_code_date'),
    ('stats.options_aggregate',  'stats.options_identity', 'anchored_by', 'for_code_date'),
    -- v_options_quote (entity seeded in 03) joins the contract chain
    ('stats.v_options_quote', 'stats.options_identity',    'computed_from', 'for_identity'),
    ('stats.v_options_quote', 'stats.options_terms',       'computed_from', 'for_terms'),
    ('stats.v_options_quote', 'stats.options_strike',      'computed_from', 'for_strike'),
    ('stats.v_options_quote', 'stats.options_settlement',  'computed_from', 'for_settlement'),
    ('stats.v_options_quote', 'stats.options_greeks',      'computed_from', 'for_greeks'),
    ('stats.v_options_quote', 'stats.options_volume_oi',   'computed_from', 'for_open_interest')
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

-- The skew family's two stores (analysis/ tables — entities owned by
-- the db_semantics_tables rule, payload in each table's DDL-file
-- banner): both are per-expiry-group SKEW statistics — IV tilt
-- across strikes (options_iv_skew_stats) and the OI/positioning-weighted
-- skew with the put-call lens (options_skewness_stats) — the same
-- business concept from two measurements (corpus descriptions + tags).
INSERT INTO llm.kg_edges (namespace, relation, sub_relation, entity_ids)
SELECT 0, e.relation, e.sub_relation, ARRAY[a.entity_id, b.entity_id]::BIGINT[]
FROM (VALUES
    ('analysis.options_iv_skew_stats', 'analysis.options_skewness_stats', 'similar_to', 'about_skew')
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
