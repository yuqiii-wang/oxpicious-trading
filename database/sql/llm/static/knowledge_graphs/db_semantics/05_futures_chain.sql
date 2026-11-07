-- ============================================================================
--  Knowledge-graph seed — namespace 0 (DB semantics): the futures chain —
--  identity, daily bars, the basis-vs-underlying study and its quintile
--  summary. Conventions: .agents/skills/kg-db-semantics. DDL:
--  13_kg_hypergraph.sql.
--
--  Idempotent — entities upsert on uq_kg_entities_ns_type_name; edges
--  insert only when the (relation, sub_relation, entity_ids) triple is
--  absent. Also carries stats.v_futures_daily's edges (the view entity is
--  seeded in 03, its member entities here).
-- ============================================================================

INSERT INTO llm.kg_entities (namespace, name, entity_type, tags, description) VALUES
    (0, 'stats.futures_identity',        'table', ARRAY['futures', '期货', 'identity', '标识', 'reference', '参考'],
     'Which futures contract traded on which day, with its product, maturity and underlying mapping — the shared identity for all futures daily tables.'),
    (0, 'stats.futures_basic_stats',     'table', ARRAY['futures', '期货', 'ohlcv', 'K线', 'settlement', '结算价', 'open interest', '持仓量'],
     'Each futures contract''s daily OHLC with settlement price, traded volume and open interest.'),
    (0, 'analysis.futures_ext',          'table', ARRAY['futures', '期货', 'basis', '基差', 'convergence', '收敛', 'mean reversion', '均值回归'],
     'Each futures contract''s daily basis versus its underlying — the price gap, its convergence rate, correlation and mean-reversion speed.'),
    (0, 'analysis.futures_gap_quintiles','table', ARRAY['futures', '期货', 'basis', '基差', 'quintile', '分位', 'convergence', '收敛'],
     'How the futures basis moves over the following days per starting-gap quintile — the basis-convergence pattern summary per contract type.')
ON CONFLICT (namespace, entity_type, name) DO UPDATE
    SET description = EXCLUDED.description,
        tags        = EXCLUDED.tags;

INSERT INTO llm.kg_edges (namespace, relation, sub_relation, entity_ids)
SELECT 0, e.relation, e.sub_relation, ARRAY[a.entity_id, b.entity_id]::BIGINT[]
FROM (VALUES
    ('stats.futures_basic_stats', 'stats.futures_identity',    'anchored_by',     'for_code_date'),
    -- Basis = futures close vs underlying: index futures read the index
    -- close; bond futures read the treasury yield converted to a price
    -- (analysis/futures/__main__.py pipeline steps 2-3)
    ('analysis.futures_ext', 'stats.futures_basic_stats',  'computed_from', 'for_close'),
    ('analysis.futures_ext', 'stats.index_basic_stats',    'computed_from', 'for_underlying_close'),
    ('analysis.futures_ext', 'stats.debt_treasury',        'computed_from', 'for_underlying_yield'),
    ('analysis.futures_gap_quintiles', 'analysis.futures_ext', 'computed_from', 'for_basis'),
    -- v_futures_daily (entity seeded in 03) joins identity + basic stats
    ('stats.v_futures_daily', 'stats.futures_identity',    'computed_from', 'for_identity'),
    ('stats.v_futures_daily', 'stats.futures_basic_stats', 'computed_from', 'for_ohlcv')
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
