-- ============================================================================
--  Knowledge-graph seed — namespace 0 (DB semantics): the options analysis
--  studies (expiry-group identity, moneyness/IV/OI/wall/vol-index gauges)
--  and the live intraday options stores. Conventions:
--  .agents/skills/kg-db-semantics. DDL: 13_kg_hypergraph.sql.
--
--  Idempotent — entities upsert on uq_kg_entities_ns_type_name; edges
--  insert only when the (relation, sub_relation, entity_ids) triple is
--  absent.
-- ============================================================================

INSERT INTO llm.kg_entities (namespace, name, entity_type, tags, description) VALUES
    (0, 'analysis.options_expiry_identity', 'table', ARRAY['options', '期权', 'expiry', '到期', 'identity', '标识', 'underlying', '标的'],
     'The collapsed expiry-group identity for the options analysis tables — one row per underlying, day, option type and expiry group.'),
    (0, 'analysis.options_moneyness_series','table', ARRAY['options', '期权', 'moneyness', '价度', 'open interest', '持仓量', 'skew', '偏度'],
     'The OI-weighted moneyness skew of each underlying''s option chain per day and expiry month — where standing open interest sits relative to spot.'),
    (0, 'analysis.options_spot_daily',      'table', ARRAY['options', '期权', 'underlying close', '标的价格', 'spot', '现货'],
     'Each option underlying''s daily close as recorded by the options loader.'),
    (0, 'analysis.options_bands',           'table', ARRAY['options', '期权', 'corridor', '走廊', 'open interest', '持仓量', 'support resistance', '支撑压力'],
     'The options-implied open-interest corridor around each underlying per day — where the chain''s mass bounds the price, where spot sits inside it and what corridor exits portend.'),
    (0, 'analysis.options_skewness_stats',  'table', ARRAY['options', '期权', 'skew', '偏度', 'moneyness', '价度', 'put call ratio', '认沽认购比'],
     'Rolling positioning and pricing skew statistics per expiry group — OI-weighted moneyness, the IV smile and the delta/gamma/vega put-call balances.'),
    (0, 'analysis.options_iv_skew_stats',   'table', ARRAY['options', '期权', 'implied volatility', '隐含波动率', 'skew', '偏度', 'iv smile', '波动率微笑'],
     'Implied-volatility skew statistics per expiry group — how implied vol tilts across strikes.'),
    (0, 'analysis.options_oi_stats',        'table', ARRAY['options', '期权', 'open interest', '持仓量', 'expiry', '到期'],
     'Open-interest levels and changes per expiry group — daily totals, 5/20-session changes and 20-session highs.'),
    (0, 'analysis.options_walls',           'table', ARRAY['options', '期权', 'wall', '墙', 'open interest', '持仓量', 'support resistance', '支撑压力'],
     'Open-interest wall zones per expiry group — the strike zones whose standing volume caps rallies (call walls) or floors dips (put walls).'),
    (0, 'analysis.options_vol_index',       'table', ARRAY['options', '期权', 'vol index', '波动率指数', 'implied volatility', '隐含波动率', 'vix'],
     'Each underlying''s daily 30-day implied-volatility index — the market''s expected fluctuation priced by its options.'),
    (0, 'analysis.options_vol_index_vs_spot_price_corr', 'table', ARRAY['options', '期权', 'vol index', '波动率指数', 'correlation', '相关性', 'spot', '现货'],
     'How strongly each underlying''s vol index moves with its spot price over trailing windows.'),
    (0, 'live.options_intraday_oi',         'table', ARRAY['options', '期权', 'open interest', '持仓量', 'intraday', '盘中', 'live', '实时'],
     'Estimated per-contract open interest through the trading day — the previous day''s OI plus cumulative traded volume, refreshed per 5-minute bar.'),
    (0, 'live.options_intraday_skewness',   'table', ARRAY['options', '期权', 'skew', '偏度', 'moneyness', '价度', 'intraday', '盘中', 'live', '实时'],
     'The intraday OI-weighted moneyness skew of the option chain per 5-minute bar — the live sibling of the daily skew statistics.')
ON CONFLICT (namespace, entity_type, name) DO UPDATE
    SET description = EXCLUDED.description,
        tags        = EXCLUDED.tags;

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
