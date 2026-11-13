-- ============================================================================
--  Knowledge-graph seed — namespace 0 (DB semantics): the security
--  classification / reference chain (sec_* tables), the composite-industry
--  daily aggregates, ETF-by-industry turnover, index ETF extensions, feeder
--  NAV, and the cross-stats composition-overlap study. Conventions:
--  .agents/skills/kg-db-semantics. DDL: kg_entities.sql + kg_edges.sql.
--
--  Idempotent — entities upsert on uq_kg_entities_ns_type_name; edges
--  insert only when the (relation, sub_relation, entity_ids) triple is
--  absent. Seeding cross_stats here (not in a later file) so the
--  market-beta study file can reference it.
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
    -- Composite-industry daily aggregates (per its DDL comment)
    ('stats.industry_basic_stats', 'stats.index_basic_stats',      'computed_from', 'for_ohlcv'),
    ('stats.industry_basic_stats', 'stats.index_valuation',        'computed_from', 'for_pe'),
    ('stats.industry_basic_stats', 'stats.stock_liquidity_margin', 'computed_from', 'for_trading_amount'),
    ('stats.industry_basic_stats', 'stats.sec_classification',     'computed_from', 'for_membership'),
    ('stats.industry_basic_stats', 'stats.sec_composition',        'computed_from', 'for_membership'),
    -- Index ETF extensions (per its DDL comment)
    ('stats.index_exts', 'stats.sec_classification',  'computed_from', 'for_etf_map'),
    ('stats.index_exts', 'stats.etf_liquidity_margin','computed_from', 'for_etf_turnover'),
    ('stats.index_exts', 'stats.sec_composition',     'computed_from', 'for_stock_num'),
    -- ETF turnover by industry
    ('stats.etf_trading_amt', 'stats.etf_liquidity_margin', 'computed_from', 'for_trading_amount'),
    ('stats.etf_trading_amt', 'stats.sec_classification',   'computed_from', 'for_industry_map'),
    -- Composition-derived reference tables (per their DDL comments)
    ('stats.sec_similars',         'stats.sec_composition',    'computed_from', 'for_shared_weights'),
    ('stats.sec_similars',         'stats.sec_classification', 'computed_from', 'for_industry'),
    ('stats.sec_board_map',        'stats.sec_composition',    'computed_from', 'for_composition_mix'),
    ('stats.sec_importance_ranks', 'stats.sec_classification',   'computed_from', 'for_classification'),
    ('stats.sec_importance_ranks', 'stats.sec_index_tags',       'computed_from', 'for_tags'),
    ('stats.sec_importance_ranks', 'stats.sec_composition',      'computed_from', 'for_weights'),
    ('stats.sec_importance_ranks', 'stats.index_basic_stats',    'computed_from', 'for_trading_amount'),
    ('stats.sec_importance_ranks', 'stats.etf_liquidity_margin', 'computed_from', 'for_trading_amount'),
    ('stats.sec_importance_ranks', 'stats.stock_liquidity_margin','computed_from', 'for_trading_amount'),
    -- Cross-stats composition overlap
    ('stats.cross_stats', 'stats.sec_composition',   'computed_from', 'for_composition_weights'),
    ('stats.cross_stats', 'stats.index_basic_stats', 'computed_from', 'for_close'),
    ('stats.cross_stats_code_summary',        'stats.cross_stats', 'computed_from', 'for_code_rollup'),
    ('stats.cross_stats_benchmark_weight_summary', 'stats.cross_stats', 'computed_from', 'for_benchmark_weights')
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
