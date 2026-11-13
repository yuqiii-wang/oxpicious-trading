-- ============================================================================
--  Knowledge-graph seed — namespace 0 (DB semantics): the futures chain —
--  identity, daily bars, the basis-vs-underlying study and its quintile
--  summary. Conventions: .agents/skills/kg-db-semantics. DDL:
--  kg_entities.sql + kg_edges.sql.
--
--  Idempotent — entities upsert on uq_kg_entities_ns_type_name; edges
--  insert only when the (relation, sub_relation, entity_ids) triple is
--  absent. Also carries stats.v_futures_daily's edges (the view entity is
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
    ('stats.futures_basic_stats', 'stats.futures_identity',    'anchored_by',     'for_code_date'),
    -- Basis = futures close vs underlying: index futures read the index
    -- close; bond futures read the treasury yield converted to a price
    -- (analysis/futures/__main__.py pipeline steps 2-3)
    ('analysis.futures_ext', 'stats.futures_basic_stats',  'computed_from', 'for_close'),
    ('analysis.futures_ext', 'stats.index_basic_stats',    'computed_from', 'for_underlying_close'),
    ('analysis.futures_ext', 'stats.debt_treasury',        'computed_from', 'for_underlying_yield'),
    ('analysis.futures_gap_quintiles', 'analysis.futures_ext', 'computed_from', 'for_basis'),
    -- v_futures_daily (entity seeded in 03) joins identity + basic stats
    ('stats.v_futures_daily', 'stats.futures_identity',    'computed_from', 'for_identity'),
    ('stats.v_futures_daily', 'stats.futures_basic_stats', 'computed_from', 'for_ohlcv')
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
