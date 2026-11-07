-- ============================================================================
--  Knowledge-graph seed — namespace 0 (DB semantics): the security
--  classification / reference chain (sec_* tables), the composite-industry
--  daily aggregates, ETF-by-industry turnover, index ETF extensions, feeder
--  NAV, and the cross-stats composition-overlap study. Conventions:
--  .agents/skills/kg-db-semantics. DDL: 13_kg_hypergraph.sql.
--
--  Idempotent — entities upsert on uq_kg_entities_ns_type_name; edges
--  insert only when the (relation, sub_relation, entity_ids) triple is
--  absent. Seeding cross_stats here (not in a later file) so the
--  market-beta study file can reference it.
-- ============================================================================

INSERT INTO llm.kg_entities (namespace, name, entity_type, tags, description) VALUES
    (0, 'stats.sec_classification',     'table', ARRAY['classification', '分类', 'sector', '板块', 'industry', '行业', 'identity', '标识', 'reference', '参考'],
     'The unified classification of every ETF, index and stock — its sector, industry, listing board and quality flags, denormalized for direct reads.'),
    (0, 'stats.sec_index_tags',         'table', ARRAY['index', '指数', 'tags', '标签', 'classification', '分类', 'sector', '板块', 'industry', '行业'],
     'The extra sector/industry tags an index carries beyond its primary classification.'),
    (0, 'stats.sec_composition',        'table', ARRAY['composition', '成分', 'holdings', '持仓', 'weight', '权重', 'constituents', '成分股', 'reference', '参考'],
     'Each ETF''s or index''s constituent holdings per composition snapshot — who is in the basket and at what weight.'),
    (0, 'stats.sec_board_map',          'table', ARRAY['board', '板块', 'listing board', '上市板块', 'classification', '分类'],
     'Each security''s listing-board mix — which boards its business spans and in what proportion.'),
    (0, 'stats.sec_etf_links',          'table', ARRAY['feeder fund', '联接基金', 'etf', 'reference', '参考'],
     'Which off-exchange feeder fund tracks which listed ETF.'),
    (0, 'stats.sec_importance_ranks',   'table', ARRAY['importance', '重要性', 'rank', '排名', 'reference', '参考'],
     'Each security''s processing-importance rank within its type — the order the platform walks the universe.'),
    (0, 'stats.sec_info',               'table', ARRAY['fund attributes', '基金属性', 'etf', 'reference', '参考'],
     'The static fund attributes of SZSE-listed ETFs.'),
    (0, 'stats.sec_reports',            'table', ARRAY['fund reports', '基金报告', 'etf', 'asset allocation', '资产配置'],
     'Each SZSE-listed ETF''s quarterly report registry — asset-allocation mix and share counts per quarter.'),
    (0, 'stats.sec_owners',             'table', ARRAY['owners', '管理人', 'fund managers', '基金管理人', 'reference', '参考'],
     'The registry of security owners — ETF fund managers and stock companies.'),
    (0, 'stats.sec_similars',           'table', ARRAY['similar codes', '相似标的', 'composition', '成分', 'peers', '同业'],
     'Each code''s closest peers by shared composition — top similar codes and similar-industry / dissimilar industry neighbors per snapshot.'),
    (0, 'stats.industry_basic_stats',   'table', ARRAY['industry', '行业', 'composite index', '行业指数', 'ohlcv', 'K线', 'pe', '市盈率', 'trading amount', '成交额'],
     'Each composite industry''s daily cross-section — member-index OHLC rebased to a common scale, mean PE, variance and total member-stock turnover, per pool size.'),
    (0, 'stats.index_exts',             'table', ARRAY['index', '指数', 'etf', 'turnover', '成交额', 'constituents', '成分股'],
     'Each index''s ETF extension metrics — how many ETFs track it, their total turnover, and its constituent stock count.'),
    (0, 'stats.etf_trading_amt',        'table', ARRAY['etf', 'turnover', '成交额', 'industry', '行业'],
     'Aggregate ETF turnover per industry per day — how much ETF money trades each theme.'),
    (0, 'stats.fund_nav',               'table', ARRAY['feeder fund', '联接基金', 'nav', '净值', 'fund', '基金'],
     'Each OTC feeder fund''s daily net asset value — the fund-side trend series where no market K-line exists.'),
    (0, 'stats.cross_stats',            'table', ARRAY['composition overlap', '持仓重合', 'shared weights', '共享权重', 'benchmark', '基准', 'correlation', '相关性'],
     'Composition-overlap and co-movement stats between each code and its benchmark — shared weights and stride-grid correlations per pair.'),
    (0, 'stats.cross_stats_dates',      'table', ARRAY['cross stats', '跨标的数据', 'dates', '日期', 'reference', '参考'],
     'The map of dates loaded into the cross-stats pair grain — the missing-date detector.'),
    (0, 'stats.cross_stats_code_summary','table', ARRAY['cross stats', '跨标的数据', 'summary', '汇总', 'benchmark', '基准'],
     'Per-code rollup of the cross-stats study — date coverage and benchmark list per code.'),
    (0, 'stats.cross_stats_benchmark_weight_summary', 'table', ARRAY['cross stats', '跨标的数据', 'shared weights', '共享权重', 'benchmark', '基准', 'summary', '汇总'],
     'Each code''s latest shared weight versus each benchmark — the benchmark-led lookup snapshot of the composition overlap.')
ON CONFLICT (namespace, entity_type, name) DO UPDATE
    SET description = EXCLUDED.description,
        tags        = EXCLUDED.tags;

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
