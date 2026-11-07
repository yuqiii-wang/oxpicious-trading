-- ============================================================================
--  Knowledge-graph seed — namespace 0 (DB semantics): the market-beta /
--  industry study chain — pairwise industry correlations (raw and
--  benchmark-offset), composition-overlap attributions, hype/drain rankings
--  (daily and seasonal), ETF turnover contribution, and the industry-pair
--  hedge ratios. Conventions: .agents/skills/kg-db-semantics. DDL:
--  13_kg_hypergraph.sql.
--
--  Idempotent — entities upsert on uq_kg_entities_ns_type_name; edges
--  insert only when the (relation, sub_relation, entity_ids) triple is
--  absent.
-- ============================================================================

INSERT INTO llm.kg_entities (namespace, name, entity_type, tags, description) VALUES
    (0, 'analysis.industry_correlations', 'table', ARRAY['industry', '行业', 'correlation', '相关性', 'market beta', '市场贝塔'],
     'How two composite industries'' price trends co-move over rolling audit windows.'),
    (0, 'analysis_composites.industry_corr_benchmark_offsets', 'table', ARRAY['industry', '行业', 'correlation', '相关性', 'benchmark offset', '基准对冲', 'opposite moves', '反向运动'],
     'Pairwise industry correlations after removing the broad-market factor — the opposite-moves pairing behind offset hedges.'),
    (0, 'analysis.industry_attributions', 'table', ARRAY['industry', '行业', 'attribution', '归因', 'composition overlap', '持仓重合', 'shared weights', '共享权重', 'benchmark', '基准'],
     'How much composition overlap each industry shares with each benchmark index, and the non-shared remainder.'),
    (0, 'analysis.industry_member_index_map', 'table', ARRAY['industry', '行业', 'member indices', '成员指数', 'shared weights', '共享权重', 'reference', '参考'],
     'Each industry''s member indices with their composition-derived shared weights, frozen for fast attribution.'),
    (0, 'analysis.industry_hypes_and_drains', 'table', ARRAY['hype', '炒作', 'drain', '失血', 'industry', '行业', 'market beta', '市场贝塔', 'rank', '排名'],
     'The top-hype and top-drain industries per day — which themes beat or lag the broad benchmark over each trailing window.'),
    (0, 'analysis.industry_hypes_seasonal', 'table', ARRAY['seasonal', '季节性', 'hype', '炒作', 'drain', '失血', 'industry', '行业', 'month', '月份'],
     'The seasonal (monthly) hype and drain industry rankings — which themes consistently beat the benchmark in each calendar month.'),
    (0, 'analysis.industry_etf_contribution', 'table', ARRAY['etf', 'turnover', '成交额', 'industry', '行业'],
     'How much ETF turnover each industry''s member indices attract per day.'),
    (0, 'analysis.pair_pct_change_ratios', 'table', ARRAY['hedge ratio', '对冲比率', 'industry', '行业', 'pair', '配对', 'market beta', '市场贝塔'],
     'Industry-pair hedge ratios over trailing windows — how many units of one industry offset another, raw and market-offset.')
ON CONFLICT (namespace, entity_type, name) DO UPDATE
    SET description = EXCLUDED.description,
        tags        = EXCLUDED.tags;

INSERT INTO llm.kg_edges (namespace, relation, sub_relation, entity_ids)
SELECT 0, e.relation, e.sub_relation, ARRAY[a.entity_id, b.entity_id]::BIGINT[]
FROM (VALUES
    -- Correlations correlate the industries' MA curves of mean_close
    ('analysis.industry_correlations', 'stats.industry_basic_stats', 'computed_from', 'for_moving_average'),
    ('analysis.industry_correlations', 'analysis_composites.industry_corr_benchmark_offsets', 'similar_to', 'about_industry_correlation'),
    -- Offset correlations read the benchmark-offset price changes
    -- (stats.cross_stats.code_price_with_benchmark_offset) + industry MA
    ('analysis_composites.industry_corr_benchmark_offsets', 'stats.cross_stats',        'computed_from', 'for_benchmark_offset'),
    ('analysis_composites.industry_corr_benchmark_offsets', 'stats.industry_basic_stats','computed_from', 'for_moving_average'),
    -- Attributions: member weights from cross_stats, benchmark weights from
    -- the composition snapshot (per its DDL comment)
    ('analysis.industry_attributions', 'stats.sec_composition', 'computed_from', 'for_composition_snapshot'),
    ('analysis.industry_attributions', 'stats.cross_stats',     'computed_from', 'for_member_weights'),
    -- Member-index map freezes composition-derived shared weights
    ('analysis.industry_member_index_map', 'stats.sec_composition', 'computed_from', 'for_shared_weights'),
    -- Hype/drain chain: daily decomposition → seasonal rankings
    ('analysis.industry_hypes_and_drains', 'analysis.industry_attributions', 'computed_from', 'for_return_decomposition'),
    ('analysis.industry_hypes_seasonal',   'analysis.industry_hypes_and_drains', 'computed_from', 'for_monthly_rank'),
    -- ETF turnover contribution aggregates index_exts
    ('analysis.industry_etf_contribution', 'stats.index_exts', 'computed_from', 'for_etf_turnover'),
    -- Hedge ratios over industry aggregate daily changes
    ('analysis.pair_pct_change_ratios', 'stats.industry_basic_stats', 'computed_from', 'for_pct_change')
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
