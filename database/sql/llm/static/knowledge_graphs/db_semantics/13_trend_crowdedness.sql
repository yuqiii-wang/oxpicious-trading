-- ============================================================================
--  Knowledge-graph seed — namespace 0 (DB semantics): the price-trend
--  classification chain (trend states and RSI tail episodes, both owned by
--  the analysis.trends component) and the market-crowdedness gauge.
--  Conventions: .agents/skills/kg-db-semantics. DDL: kg_entities.sql + kg_edges.sql.
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
    -- Trend states read RSI momentum episodes + the price series (the
    -- analysis.trends component; MA overextension vs its own trailing range)
    ('analysis.trend_days', 'analysis.mov_ave_rsi',   'computed_from', 'for_rsi'),
    ('analysis.trend_days', 'stats.index_basic_stats','computed_from', 'for_close'),
    ('analysis.trend_days', 'stats.etf_basic_stats',  'computed_from', 'for_close'),
    ('analysis.trend_days', 'stats.stock_basic_stats','computed_from', 'for_close'),
    ('analysis.trend_days', 'analysis.trend_episode_days', 'similar_to', 'about_trend'),
    -- RSI tail episodes ride the full-history RSI ranks rebuild
    ('analysis.trend_episode_days', 'analysis.mov_ave_rsi_ranks', 'computed_from', 'for_rsi_ranks'),
    -- Crowdedness pillars (per analysis/market_crowdedness/__main__.py):
    -- pool membership from composition/classification, turnover amounts,
    -- stock PE for the valuation pillar
    ('analysis.market_crowdedness', 'stats.stock_liquidity_margin', 'computed_from', 'for_trading_amount'),
    ('analysis.market_crowdedness', 'stats.sec_composition',        'computed_from', 'for_pool_membership'),
    ('analysis.market_crowdedness', 'stats.sec_classification',     'computed_from', 'for_industry_map'),
    ('analysis.market_crowdedness', 'stats.stock_basic_stats',      'computed_from', 'for_pe')
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
