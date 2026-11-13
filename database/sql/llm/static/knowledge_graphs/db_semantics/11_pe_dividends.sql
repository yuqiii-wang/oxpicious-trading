-- ============================================================================
--  Knowledge-graph seed — namespace 0 (DB semantics): the PE and dividend
--  studies — the unified PE series, its monthly percentile bands and band
--  breaks, the trailing dividend yield, the cum-dividend fair-value curve,
--  the delivery audit, and the stock dividend history beneath them.
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
    -- Unified PE sources per sec_type (per its DDL comment)
    ('analysis.pe', 'stats.index_valuation',     'computed_from', 'for_pe'),
    ('analysis.pe', 'stats.etf_basic_stats',     'computed_from', 'for_pe'),
    ('analysis.pe', 'stats.stock_basic_stats',   'computed_from', 'for_pe'),
    ('analysis.pe', 'stats.industry_basic_stats','computed_from', 'for_pe'),
    -- PE band + streak chain
    ('analysis.pe_pct',         'analysis.pe',         'computed_from', 'for_pe'),
    ('analysis.pe_pct_streaks', 'analysis.pe_pct',     'computed_from', 'for_band'),
    ('analysis.pe_pct_streaks', 'analysis.pe',         'computed_from', 'for_pe'),
    ('analysis.pe_codes',       'analysis.pe',         'computed_from', 'for_pe'),
    ('analysis.pe_codes',       'analysis.dividends',  'computed_from', 'for_dividend_yield'),
    -- Trailing dividend yield: per-share dividends, composition weights for
    -- the index cap-weighting, closes for the denominator
    ('analysis.dividends', 'stats.stock_dividends',    'computed_from', 'for_dividend'),
    ('analysis.dividends', 'stats.etf_adjustment',     'computed_from', 'for_dividend'),
    ('analysis.dividends', 'stats.sec_composition',    'computed_from', 'for_member_weights'),
    ('analysis.dividends', 'stats.index_basic_stats',  'computed_from', 'for_close'),
    ('analysis.dividends', 'stats.etf_basic_stats',    'computed_from', 'for_close'),
    ('analysis.dividends', 'stats.stock_basic_stats',  'computed_from', 'for_close'),
    -- Cum-dividend fair-value curve (ETF scope: ex-dates + closes)
    ('analysis.dividend_est', 'stats.etf_adjustment',  'computed_from', 'for_dividend'),
    ('analysis.dividend_est', 'stats.etf_basic_stats', 'computed_from', 'for_close'),
    -- Delivery audit: yields + per-share events + eps at ex-dates
    ('analysis.dividend_deliveries', 'analysis.dividends',     'computed_from', 'for_dividend_yield'),
    ('analysis.dividend_deliveries', 'stats.stock_dividends',  'computed_from', 'for_dividend'),
    ('analysis.dividend_deliveries', 'stats.stock_basic_stats','computed_from', 'for_eps')
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
