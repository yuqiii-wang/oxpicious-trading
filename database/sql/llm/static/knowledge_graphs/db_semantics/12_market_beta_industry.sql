-- ============================================================================
--  Knowledge-graph seed — namespace 0 (DB semantics): the market-beta /
--  industry study chain — pairwise industry correlations (raw and
--  benchmark-offset), composition-overlap attributions, hype/drain rankings
--  (daily and seasonal), ETF turnover contribution, the industry-pair
--  hedge ratios, and the per-industry stock rise/drop distribution audit.
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
    ('analysis.pair_pct_change_ratios', 'stats.industry_basic_stats', 'computed_from', 'for_pct_change'),
    -- Stock change distribution: buckets each stock's daily pct_change
    -- (stats.stock_basic_stats) into the industry it maps to via
    -- sec_classification (per analysis/market_beta/stock_attribution)
    ('analysis.industry_stock_change_dist', 'stats.stock_basic_stats',  'computed_from', 'for_pct_change'),
    ('analysis.industry_stock_change_dist', 'stats.sec_classification', 'computed_from', 'for_industry_map'),
    -- Same breadth concept as the market-wide adv/dec counts — the
    -- whole-universe rise/drop split vs the per-industry buckets
    -- (SYMMETRIC family; seed one direction)
    ('analysis.industry_stock_change_dist', 'analysis.market_participation', 'similar_to', 'about_breadth')
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
