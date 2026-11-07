-- ============================================================================
--  Knowledge-graph seed — namespace 0 (DB semantics): the price-trend
--  classification chain (trend states and RSI tail episodes, both owned by
--  the analysis.trends component) and the market-crowdedness gauge.
--  Conventions: .agents/skills/kg-db-semantics. DDL: 13_kg_hypergraph.sql.
--
--  Idempotent — entities upsert on uq_kg_entities_ns_type_name; edges
--  insert only when the (relation, sub_relation, entity_ids) triple is
--  absent.
-- ============================================================================

INSERT INTO llm.kg_entities (namespace, name, entity_type, tags, description) VALUES
    (0, 'analysis.trend_days',          'table', ARRAY['trend', '趋势', 'rise fall', '涨跌', 'state', '状态', 'top bottom', '顶底'],
     'Each security-day''s price trend classification — rising, falling, topped or bottomed, from RSI momentum episodes and overextension versus its own range.'),
    (0, 'analysis.trend_episode_days',  'table', ARRAY['rsi', 'episode', '趋势段', 'tail', '尾部', 'momentum', '动量'],
     'The RSI-tail episode days per security — which days sit in the extreme ranks of the code''s own RSI history and whether they passed the side-mirrored pattern test.'),
    (0, 'analysis.market_crowdedness',  'table', ARRAY['crowdedness', '拥挤度', 'concentration', '集中度', 'comovement', '共振', 'market sentiment', '市场情绪'],
     'Each index or industry''s daily crowdedness score — how concentrated, inflowed, co-moving and expensive its hottest names are versus the pool''s own history.')
ON CONFLICT (namespace, entity_type, name) DO UPDATE
    SET description = EXCLUDED.description,
        tags        = EXCLUDED.tags;

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
