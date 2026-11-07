-- ============================================================================
--  Knowledge-graph seed — namespace 0 (DB semantics): the intraday 5-minute
--  bar tables and the reconstruction views that join the split daily tables
--  back into one row. Conventions: .agents/skills/kg-db-semantics — one
--  entity per table/view, ONE biz sentence, tags = business + query-usage
--  terms, edges = relation verb + prepositional sub_relation. DDL:
--  13_kg_hypergraph.sql.
--
--  Idempotent — entities upsert on uq_kg_entities_ns_type_name (ids stay
--  stable for edge member references); edges insert only when the
--  (relation, sub_relation, entity_ids) triple is absent. Edges whose
--  counterpart entity is seeded in a LATER file live in that later file
--  (member resolution joins existing rows only).
-- ============================================================================

INSERT INTO llm.kg_entities (namespace, name, entity_type, tags, description) VALUES
    (0, 'stats.index_intraday_5min',   'table', ARRAY['index', '指数', 'intraday', '盘中', 'five-minute bars', '五分钟K线', 'ohlcv', 'K线'],
     'Each index''s 5-minute intraday price bars within the trading day.'),
    (0, 'stats.etf_intraday_5min',     'table', ARRAY['etf', 'intraday', '盘中', 'five-minute bars', '五分钟K线', 'ohlcv', 'K线'],
     'Each ETF''s 5-minute intraday price bars within the trading day.'),
    (0, 'stats.stock_intraday_5min',   'table', ARRAY['stock', '股票', 'intraday', '盘中', 'five-minute bars', '五分钟K线', 'ohlcv', 'K线'],
     'Each stock''s 5-minute intraday price bars within the trading day.'),
    (0, 'stats.options_intraday_5min', 'table', ARRAY['options', '期权', 'intraday', '盘中', 'five-minute bars', '五分钟K线'],
     'Each listed option contract''s 5-minute intraday price bars within the trading day.'),
    (0, 'stats.v_debt_daily',     'view', ARRAY['debt', '债券', 'rates', '利率', 'central bank', '央行', 'yield curve', '收益率曲线'],
     'Each trading day''s full debt-market record in one row — policy operations, repo stock, SHIBOR and the treasury yield curve joined per date.'),
    (0, 'stats.v_etf_margin',     'view', ARRAY['etf', 'ohlcv', 'K线', 'margin', '融资融券', 'adjusted price', '复权'],
     'Each ETF''s full daily record on one row — K-line prices, split/dividend adjustments and margin balances joined per code per date.'),
    (0, 'stats.v_stock_daily',    'view', ARRAY['stock', '股票', 'ohlcv', 'K线', 'margin', '融资融券', 'adjusted price', '复权'],
     'Each stock''s full daily record on one row — K-line prices, split/dividend adjustments and margin balances joined per code per date.'),
    (0, 'stats.v_futures_daily',  'view', ARRAY['futures', '期货', 'ohlcv', 'K线', 'settlement', '结算价', 'open interest', '持仓量'],
     'Each futures contract''s full daily record on one row — contract identity and OHLCV with settlement and open interest joined per code per date.'),
    (0, 'stats.v_options_quote',  'view', ARRAY['options', '期权', 'quote', '行情', 'greeks', '希腊字母', 'open interest', '持仓量'],
     'Each option contract''s full daily record on one row — contract terms, strike, settlement, greeks and volume/open interest joined per code per date.')
ON CONFLICT (namespace, entity_type, name) DO UPDATE
    SET description = EXCLUDED.description,
        tags        = EXCLUDED.tags;

-- Only edges whose counterpart entities already exist (01/02 chains).
-- The debt / futures / options view edges live in their chains' files.
INSERT INTO llm.kg_edges (namespace, relation, sub_relation, entity_ids)
SELECT 0, e.relation, e.sub_relation, ARRAY[a.entity_id, b.entity_id]::BIGINT[]
FROM (VALUES
    ('stats.v_etf_margin',  'stats.etf_identity',         'computed_from', 'for_identity'),
    ('stats.v_etf_margin',  'stats.etf_basic_stats',      'computed_from', 'for_ohlcv'),
    ('stats.v_etf_margin',  'stats.etf_adjustment',       'computed_from', 'for_adjusted_close'),
    ('stats.v_etf_margin',  'stats.etf_liquidity_margin', 'computed_from', 'for_margin'),
    ('stats.v_stock_daily', 'stats.stock_identity',         'computed_from', 'for_identity'),
    ('stats.v_stock_daily', 'stats.stock_basic_stats',      'computed_from', 'for_ohlcv'),
    ('stats.v_stock_daily', 'stats.stock_adjustment',       'computed_from', 'for_adjusted_close'),
    ('stats.v_stock_daily', 'stats.stock_liquidity_margin', 'computed_from', 'for_margin')
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
