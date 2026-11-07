-- ============================================================================
--  Knowledge-graph seed — namespace 0 (DB semantics): the margin (融资融券)
--  chain — per-index / per-industry margin series, the trend-detection
--  input, the trend-episode summary, and the exchanges' official market-wide
--  totals. Conventions: .agents/skills/kg-db-semantics. DDL:
--  13_kg_hypergraph.sql.
--
--  Idempotent — entities upsert on uq_kg_entities_ns_type_name; edges
--  insert only when the (relation, sub_relation, entity_ids) triple is
--  absent.
-- ============================================================================

INSERT INTO llm.kg_entities (namespace, name, entity_type, tags, description) VALUES
    (0, 'analysis.margin_index_series',  'table', ARRAY['margin', '融资融券', 'index', '指数', 'financing balance', '融资余额'],
     'Each index''s daily margin-financing curve — member stocks'' balances weighted by index membership, or tracking-ETF margin for broad indices.'),
    (0, 'analysis.margin_industry_stats','table', ARRAY['margin', '融资融券', 'industry', '行业', 'financing balance', '融资余额'],
     'Each industry''s daily margin financing — stock and ETF member balances and buys summed, with member-normalized curves alongside.'),
    (0, 'analysis.margin_tech_stats',    'table', ARRAY['margin', '融资融券', 'slope', '斜率', 'trend detection', '趋势检测'],
     'The smoothed slope and its significance on each security''s margin balance curve — the trend-detection input for margin episode detection.'),
    (0, 'analysis.margin_changes',       'table', ARRAY['margin', '融资融券', 'trend', '趋势', 'episode', '趋势段', 'financing flow', '融资买入'],
     'Each security''s significant margin-balance trends — sustained rise/fall episodes with the buying flow behind them.'),
    (0, 'analysis.exchange_margin_summary','table', ARRAY['margin', '融资融券', 'exchange', '交易所', 'market-wide totals', '市场汇总', 'securities lending', '融券'],
     'Each exchange''s official market-wide margin totals per day — financing plus securities lending across the whole market.')
ON CONFLICT (namespace, entity_type, name) DO UPDATE
    SET description = EXCLUDED.description,
        tags        = EXCLUDED.tags;

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
