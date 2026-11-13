-- ============================================================================
--  Knowledge-graph seed — namespace 0 (DB semantics): the ETF and stock
--  daily-bars chains (identity / basic_stats / tech_stats / adjustment /
--  liquidity+margin) and the market-regime registry. Conventions:
--  .agents/skills/kg-db-semantics — one entity per table, ONE biz sentence,
--  tags = business + query-usage terms, edges = relation verb +
--  prepositional sub_relation. DDL: kg_entities.sql + kg_edges.sql.
--
--  Idempotent — entities upsert on uq_kg_entities_ns_type_name (ids stay
--  stable for edge member references); edges insert only when the
--  (relation, sub_relation, entity_ids) triple is absent.
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
    -- ETF chain anchors (FKs on (code, date) → etf_identity)
    ('stats.etf_basic_stats',       'stats.etf_identity', 'anchored_by',   'for_code_date'),
    ('stats.etf_tech_stats',        'stats.etf_identity', 'anchored_by',   'for_code_date'),
    ('stats.etf_adjustment',        'stats.etf_identity', 'anchored_by',   'for_code_date'),
    ('stats.etf_liquidity_margin',  'stats.etf_identity', 'anchored_by',   'for_code_date'),
    -- Stock chain anchors (FKs on (code, date) → stock_identity)
    ('stats.stock_basic_stats',      'stats.stock_identity', 'anchored_by', 'for_code_date'),
    ('stats.stock_tech_stats',       'stats.stock_identity', 'anchored_by', 'for_code_date'),
    ('stats.stock_adjustment',       'stats.stock_identity', 'anchored_by', 'for_code_date'),
    ('stats.stock_margin',           'stats.stock_identity', 'anchored_by', 'for_code_date'),
    ('stats.stock_liquidity_margin', 'stats.stock_identity', 'anchored_by', 'for_code_date'),
    -- Stock tech stats are computed from stock closes (DDL comment)
    ('stats.stock_tech_stats',       'stats.stock_basic_stats', 'computed_from', 'for_close'),
    -- MA-spread study sources beyond the index path (per its DDL):
    -- ETF prices = COALESCE(etf_adjustment.adj_close, etf_basic_stats.close),
    -- stock prices = stock_basic_stats.close
    ('analysis.mov_ave_spreads_detail', 'stats.etf_adjustment',    'computed_from', 'for_adjusted_close'),
    ('analysis.mov_ave_spreads_detail', 'stats.etf_basic_stats',   'computed_from', 'for_close'),
    ('analysis.mov_ave_spreads_detail', 'stats.stock_basic_stats', 'computed_from', 'for_close'),
    -- RSI and momentum reuse the parent's source prices (their docstrings)
    ('analysis.mov_ave_rsi',         'stats.etf_adjustment',    'computed_from', 'for_adjusted_close'),
    ('analysis.mov_ave_rsi',         'stats.etf_basic_stats',   'computed_from', 'for_close'),
    ('analysis.mov_ave_rsi',         'stats.stock_basic_stats', 'computed_from', 'for_close'),
    ('analysis.mov_ave_momentum',    'stats.etf_adjustment',    'computed_from', 'for_adjusted_close'),
    ('analysis.mov_ave_momentum',    'stats.etf_basic_stats',   'computed_from', 'for_close'),
    ('analysis.mov_ave_momentum',    'stats.stock_basic_stats', 'computed_from', 'for_close'),
    -- Momentum's volatility-adaptive lookbacks read regimes + vol
    ('analysis.mov_ave_momentum',    'stats.market_regimes',    'computed_from', 'for_regime')
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
