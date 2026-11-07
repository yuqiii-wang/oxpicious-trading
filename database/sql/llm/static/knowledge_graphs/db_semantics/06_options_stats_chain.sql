-- ============================================================================
--  Knowledge-graph seed — namespace 0 (DB semantics): the contract-level
--  options chain in stats (identity / terms / strike / settlement / greeks /
--  volume+oi / per-underlying aggregate). Conventions:
--  .agents/skills/kg-db-semantics. DDL: 13_kg_hypergraph.sql.
--
--  Idempotent — entities upsert on uq_kg_entities_ns_type_name; edges
--  insert only when the (relation, sub_relation, entity_ids) triple is
--  absent. Also carries stats.v_options_quote's edges (the view entity is
--  seeded in 03, its member entities here).
-- ============================================================================

INSERT INTO llm.kg_entities (namespace, name, entity_type, tags, description) VALUES
    (0, 'stats.options_identity',  'table', ARRAY['options', '期权', 'identity', '标识', 'reference', '参考'],
     'Which option contract traded on which day — the shared identity for all options daily tables.'),
    (0, 'stats.options_terms',     'table', ARRAY['options', '期权', 'contract terms', '合约条款', 'expiry', '到期日', 'underlying', '标的'],
     'Each option''s contract terms — its underlying, option type, expiry month and expiry date.'),
    (0, 'stats.options_strike',    'table', ARRAY['options', '期权', 'strike', '行权价'],
     'Each option''s strike price.'),
    (0, 'stats.options_settlement','table', ARRAY['options', '期权', 'settlement', '结算价', 'underlying close', '标的价格'],
     'Each option''s daily settlement price together with its underlying''s close that day.'),
    (0, 'stats.options_greeks',    'table', ARRAY['options', '期权', 'greeks', '希腊字母', 'implied volatility', '隐含波动率'],
     'Each option''s per-day risk greeks — delta, gamma, vega, theta, rho — and its implied volatility.'),
    (0, 'stats.options_volume_oi', 'table', ARRAY['options', '期权', 'volume', '成交量', 'open interest', '持仓量'],
     'Each option''s daily traded volume and end-of-day open interest.'),
    (0, 'stats.options_aggregate', 'table', ARRAY['options', '期权', 'aggregate', '汇总', 'underlying', '标的'],
     'Each option underlying''s daily whole-chain aggregate context.')
ON CONFLICT (namespace, entity_type, name) DO UPDATE
    SET description = EXCLUDED.description,
        tags        = EXCLUDED.tags;

INSERT INTO llm.kg_edges (namespace, relation, sub_relation, entity_ids)
SELECT 0, e.relation, e.sub_relation, ARRAY[a.entity_id, b.entity_id]::BIGINT[]
FROM (VALUES
    -- Sub-tables FK options_identity on (contract_code, date)
    ('stats.options_terms',      'stats.options_identity', 'anchored_by', 'for_code_date'),
    ('stats.options_strike',     'stats.options_identity', 'anchored_by', 'for_code_date'),
    ('stats.options_settlement', 'stats.options_identity', 'anchored_by', 'for_code_date'),
    ('stats.options_greeks',     'stats.options_identity', 'anchored_by', 'for_code_date'),
    ('stats.options_volume_oi',  'stats.options_identity', 'anchored_by', 'for_code_date'),
    ('stats.options_aggregate',  'stats.options_identity', 'anchored_by', 'for_code_date'),
    -- v_options_quote (entity seeded in 03) joins the contract chain
    ('stats.v_options_quote', 'stats.options_identity',    'computed_from', 'for_identity'),
    ('stats.v_options_quote', 'stats.options_terms',       'computed_from', 'for_terms'),
    ('stats.v_options_quote', 'stats.options_strike',      'computed_from', 'for_strike'),
    ('stats.v_options_quote', 'stats.options_settlement',  'computed_from', 'for_settlement'),
    ('stats.v_options_quote', 'stats.options_greeks',      'computed_from', 'for_greeks'),
    ('stats.v_options_quote', 'stats.options_volume_oi',   'computed_from', 'for_open_interest')
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
