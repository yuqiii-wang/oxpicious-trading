-- ============================================================================
--  Knowledge-graph seed — namespace 0 (DB semantics): the database describes
--  itself. One of the static/knowledge_graphs/ seed files — DDL:
--  kg_entities.sql + kg_edges.sql.
--
--  Namespace-0 conventions (what its records mean):
--    - ONE ENTITY = ONE TABLE. entity_type 'table', name = schema-qualified
--      table name, description = ONE sentence of the table's BUSINESS
--      meaning — never ops (upserts, --force) nor implementation
--      (keys, FKs, partitions, indexes). tags = BUSINESS-domain terms —
--      the table's content and HOW QUERIES USE it, as a trader names
--      them (e.g. {index, ohlcv, moving average}, or 'identity' /
--      'reference' for lookup-anchor tables); no UI, no operations
--      (upserts, --force), no storage mechanics (keys, FKs,
--      partitions).
--    - EDGES = TABLE RELATIONSHIPS, head-first (entity_ids[0] = subject).
--      relation keeps the plain VERB; sub_relation carries the narrowed
--      semantics as a PREPOSITIONAL PHRASE (for_… / about_…; NULL = bare
--      verb, not yet narrowed):
--      'computed_from'  derived table → its source table. sub_relation =
--                       what the derivation is FOR: for_close /
--                       for_ohlcv / for_pe / for_moving_average /
--                       for_identity
--      'anchored_by'    fact/reference table → its identity table.
--                       sub_relation = the anchor grain:
--                       for_code_date = rows keyed (code, date)
--      'similar_to'     analysis tables that BOTH INDICATE the same
--                       business concept — sub_relation names what the
--                       similarity is ABOUT: about_momentum
--                       (rsi ↔ momentum).
--                       SYMMETRIC: seed one direction, walkers traverse
--                       either way. relation / sub_relation vocabularies
--                       are open TEXT.
--
--  namespace 0 is the built-in DB-semantics subgraph. kg_namespace is
--  deliberately NOT yet registered in label_dim (kg_entities.sql + kg_edges.sql
--  header) — the codes stay documented in the column comments until the
--  first KG writer ships.
--
--  Idempotent — entities upsert in place on uq_kg_entities_ns_type_name
--  (entity_id / created_at untouched, so edge member ids stay stable);
--  edges insert only when the same (namespace, relation, entity_ids)
--  triple is absent. Scope: the index daily-bars chain — its tables, the
--  reconstructed v_index_daily view, and the analysis studies derived
--  from them.
-- ============================================================================
--  2026-11-09: the 'table' entities migrated to the corpus rule
--  db_semantics_tables; since 2026-11-10 the payloads live in the
--  tables' own database/sql/ DDL files — ONE TABLE = ONE FILE, payload
--  in the leading banner (formerly the parallel database/kg_tables/
--  corpus, filename = the schema-qualified name). This seed keeps
--  only what doesn't fit a rule: the edge INSERTs and the DB
--  view entities. The rule's ledger owns the migrated tables;
--  edges here still resolve them BY NAME.

INSERT INTO llm.kg_entities (namespace, name, entity_type, tags, description) VALUES
    (0, 'stats.v_index_daily', 'view',  ARRAY['index', '指数', 'ohlcv', 'K线', 'moving average', '均线', 'valuation', '估值'],
     'Each index''s full daily record on one row — K-line prices, traded amount, PE and moving averages joined per code per date.')
ON CONFLICT (namespace, entity_type, name) DO UPDATE
    SET description = EXCLUDED.description,
        tags        = EXCLUDED.tags;

-- Edges resolve the entity ids from the upsert above (no FK can span the
-- array — member resolution is the writer's job, same namespace only).
INSERT INTO llm.kg_edges (namespace, relation, sub_relation, entity_ids)
SELECT 0, e.relation, e.sub_relation, ARRAY[a.entity_id, b.entity_id]::BIGINT[]
FROM (VALUES
    ('stats.index_basic_stats',       'stats.index_identity', 'anchored_by',   'for_code_date'),
    ('stats.index_valuation',         'stats.index_identity', 'anchored_by',   'for_code_date'),
    ('stats.index_tech_stats',        'stats.index_identity', 'anchored_by',   'for_code_date'),
    ('stats.exchange_trading_amt',    'stats.index_identity', 'anchored_by',   'for_code_date'),
    ('analysis.mov_ave_spreads_detail', 'stats.index_basic_stats', 'computed_from', 'for_close'),
    ('analysis.mov_ave_spreads_detail', 'stats.index_tech_stats',  'computed_from', 'for_moving_average'),
    ('analysis.mov_ave_rsi',          'stats.index_basic_stats', 'computed_from', 'for_close'),
    ('analysis.mov_ave_momentum',     'stats.index_basic_stats', 'computed_from', 'for_close'),
    ('analysis.mov_ave_rsi',          'analysis.mov_ave_momentum',   'similar_to', 'about_momentum'),
    ('stats.v_index_daily',           'stats.index_identity',    'computed_from', 'for_identity'),
    ('stats.v_index_daily',           'stats.index_basic_stats', 'computed_from', 'for_ohlcv'),
    ('stats.v_index_daily',           'stats.index_valuation',   'computed_from', 'for_pe'),
    ('stats.v_index_daily',           'stats.index_tech_stats',  'computed_from', 'for_moving_average')
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