-- ============================================================================
--  Knowledge-graph seed — namespace 0 (DB semantics): the options analysis
--  studies (expiry-group identity, moneyness/IV/OI/wall/vol-index gauges)
--  and the live intraday options stores. Conventions:
--  .agents/skills/kg-db-semantics. DDL: kg_entities.sql + kg_edges.sql.
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
    -- Expiry-group identity derives from the contract terms' expiry mapping;
    -- the FK'd analysis tables anchor on it
    ('analysis.options_expiry_identity', 'stats.options_terms',              'computed_from', 'for_expiry_group'),
    ('analysis.options_skewness_stats',  'analysis.options_expiry_identity', 'anchored_by',   'for_expiry_group'),
    ('analysis.options_iv_skew_stats',   'analysis.options_expiry_identity', 'anchored_by',   'for_expiry_group'),
    ('analysis.options_walls',           'analysis.options_expiry_identity', 'anchored_by',   'for_expiry_group'),
    -- Moneyness / bands / skew read the contract chain's facets
    ('analysis.options_moneyness_series', 'stats.options_strike',     'computed_from', 'for_strike'),
    ('analysis.options_moneyness_series', 'stats.options_volume_oi',  'computed_from', 'for_open_interest'),
    ('analysis.options_moneyness_series', 'stats.options_greeks',     'computed_from', 'for_implied_vol'),
    ('analysis.options_moneyness_series', 'stats.options_settlement', 'computed_from', 'for_underlying_close'),
    ('analysis.options_spot_daily',       'stats.options_settlement', 'computed_from', 'for_underlying_close'),
    ('analysis.options_bands',            'stats.options_volume_oi',  'computed_from', 'for_open_interest'),
    ('analysis.options_bands',            'stats.options_settlement', 'computed_from', 'for_underlying_close'),
    ('analysis.options_bands',            'stats.options_strike',     'computed_from', 'for_strike'),
    ('analysis.options_bands',            'stats.options_greeks',     'computed_from', 'for_implied_vol'),
    ('analysis.options_bands',            'analysis.options_walls',   'computed_from', 'for_wall_context'),
    ('analysis.options_skewness_stats',   'stats.options_greeks',     'computed_from', 'for_greeks'),
    ('analysis.options_skewness_stats',   'stats.options_volume_oi',  'computed_from', 'for_open_interest'),
    ('analysis.options_skewness_stats',   'stats.options_strike',     'computed_from', 'for_strike'),
    ('analysis.options_iv_skew_stats',    'stats.options_greeks',     'computed_from', 'for_implied_vol'),
    ('analysis.options_oi_stats',         'stats.options_volume_oi',  'computed_from', 'for_open_interest'),
    ('analysis.options_walls',            'stats.options_volume_oi',  'computed_from', 'for_open_interest'),
    -- Vol index (model-free IV) and its correlation with spot
    ('analysis.options_vol_index', 'stats.options_settlement', 'computed_from', 'for_settlement_price'),
    ('analysis.options_vol_index', 'stats.options_strike',     'computed_from', 'for_strike'),
    ('analysis.options_vol_index_vs_spot_price_corr', 'analysis.options_vol_index', 'computed_from', 'for_vol_index'),
    ('analysis.options_vol_index_vs_spot_price_corr', 'analysis.options_spot_daily', 'computed_from', 'for_underlying_close'),
    ('analysis.options_vol_index', 'analysis.options_iv_skew_stats', 'similar_to', 'about_implied_vol'),
    -- Live intraday stores
    ('live.options_intraday_oi',       'stats.options_volume_oi',    'computed_from', 'for_prev_day_oi'),
    ('live.options_intraday_skewness', 'live.options_intraday_oi',   'computed_from', 'for_open_interest'),
    ('live.options_intraday_skewness', 'stats.options_volume_oi',    'computed_from', 'for_open_interest'),
    ('live.options_intraday_skewness', 'stats.etf_intraday_5min',    'computed_from', 'for_spot'),
    ('live.options_intraday_skewness', 'stats.index_intraday_5min',  'computed_from', 'for_spot')
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
