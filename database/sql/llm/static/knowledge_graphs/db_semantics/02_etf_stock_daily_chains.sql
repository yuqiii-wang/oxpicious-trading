-- ============================================================================
--  Knowledge-graph seed — namespace 0 (DB semantics): the ETF and stock
--  daily-bars chains (identity / basic_stats / tech_stats / adjustment /
--  liquidity+margin) and the market-regime registry. Conventions:
--  .agents/skills/kg-db-semantics — one entity per table, ONE biz sentence,
--  tags = business + query-usage terms, edges = relation verb +
--  prepositional sub_relation. DDL: 13_kg_hypergraph.sql.
--
--  Idempotent — entities upsert on uq_kg_entities_ns_type_name (ids stay
--  stable for edge member references); edges insert only when the
--  (relation, sub_relation, entity_ids) triple is absent.
-- ============================================================================

INSERT INTO llm.kg_entities (namespace, name, entity_type, tags, description) VALUES
    (0, 'stats.etf_identity',           'table', ARRAY['etf', 'identity', '标识', 'reference', '参考'],
     'Which ETF traded on which day — the shared identity for all ETF daily tables.'),
    (0, 'stats.etf_basic_stats',        'table', ARRAY['etf', 'ohlcv', 'K线', 'pe', '市盈率'],
     'Each ETF''s daily OHLC prices plus its holdings'' harmonic-weighted price-earnings ratio.'),
    (0, 'stats.etf_tech_stats',         'table', ARRAY['etf', 'indicators', '技术指标', 'moving average', '均线', 'ema'],
     'Each ETF''s daily moving-average and EMA values.'),
    (0, 'stats.etf_adjustment',         'table', ARRAY['etf', 'adjusted price', '复权', 'dividend', '分红'],
     'Each ETF''s split and dividend adjustments that splice distributions back into its price series.'),
    (0, 'stats.etf_liquidity_margin',   'table', ARRAY['etf', 'trading amount', '成交额', 'margin', '融资融券'],
     'Each ETF''s daily traded shares and amount plus its margin-trading balances.'),
    (0, 'stats.stock_identity',         'table', ARRAY['stock', '股票', 'identity', '标识', 'reference', '参考'],
     'Which stock traded on which day — the shared identity for all stock daily tables.'),
    (0, 'stats.stock_basic_stats',      'table', ARRAY['stock', '股票', 'ohlcv', 'K线', 'price change', '涨跌幅', 'pe', '市盈率'],
     'Each stock''s daily OHLC with percent change and price-earnings ratio.'),
    (0, 'stats.stock_tech_stats',       'table', ARRAY['stock', '股票', 'indicators', '技术指标', 'moving average', '均线', 'ema'],
     'Each stock''s daily moving-average and EMA values.'),
    (0, 'stats.stock_adjustment',       'table', ARRAY['stock', '股票', 'adjusted price', '复权', 'dividend', '分红'],
     'Each stock''s split and dividend adjustments that splice distributions back into its price series.'),
    (0, 'stats.stock_margin',           'table', ARRAY['stock', '股票', 'margin', '融资融券'],
     'Each stock''s margin-trading and short-selling balances (融资融券).'),
    (0, 'stats.stock_liquidity_margin', 'table', ARRAY['stock', '股票', 'trading amount', '成交额', 'margin', '融资融券'],
     'Each stock''s daily traded shares and amount plus its margin-trading balances.'),
    (0, 'stats.market_regimes',         'table', ARRAY['market', '市场', 'regime', '市场状态', 'volatility', '波动率'],
     'Each security-day classified into one market regime — hot, panic, quiet or calm — with the volatility and traded-amount evidence.'),
    (0, 'stats.market_regime_spans',    'table', ARRAY['market', '市场', 'regime', '市场状态'],
     'Contiguous same-regime day spans per security — the bands charts shade regimes with.')
ON CONFLICT (namespace, entity_type, name) DO UPDATE
    SET description = EXCLUDED.description,
        tags        = EXCLUDED.tags;

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
