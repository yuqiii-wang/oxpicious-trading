-- ============================================================================
--  Knowledge-graph seed — namespace 0 (DB semantics): the strategy
--  backtest/training chain — run identity, results, daily portfolio state,
--  trade decisions, risk metrics and their factor/period lenses, the
--  per-security algo parameter overrides, and the training process log.
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



-- The strategy DB views (the DB-view lane — schema-qualified names,
-- owned by the seeds like the stats.v_* views; descriptions from the
-- views' own DDL header, database/sql/strategy/trade_decision_seqs/
-- 08_views.sql).
INSERT INTO llm.kg_entities (namespace, name, entity_type, tags, description) VALUES
    (0, 'strategy.v_trade_decision_full', 'view',  ARRAY['strategy', '策略', 'backtest', '回测', 'trade decision', '交易决策', 'run context', '运行上下文'],
     'Each backtest decision with its run context on one row — strategy name, params and buy-cost anchors joined from identity and results alongside the decision''s normalized fill price.'),
    (0, 'strategy.v_strategy_risk_full',  'view',  ARRAY['strategy', '策略', 'risk', '风险', 'pnl', '盈亏', 'risk metrics', '风险指标'],
     'Each risk snapshot with its run context and P&L summary on one row — identity, results and strategy_risks joined so the risk metrics read alongside realized P&L.')
ON CONFLICT (namespace, entity_type, name) DO UPDATE
    SET description = EXCLUDED.description,
        tags        = EXCLUDED.tags;

INSERT INTO llm.kg_edges (namespace, relation, sub_relation, entity_ids)
SELECT 0, e.relation, e.sub_relation, ARRAY[a.entity_id, b.entity_id]::BIGINT[]
FROM (VALUES
    ('strategy.strategy_results',    'strategy.strategy_identity', 'anchored_by', 'for_run'),
    ('strategy.strategy_daily',      'strategy.strategy_identity', 'anchored_by', 'for_run'),
    ('strategy.trade_decision',      'strategy.strategy_identity', 'anchored_by', 'for_run'),
    ('strategy.strategy_risks',      'strategy.strategy_identity', 'anchored_by', 'for_run'),
    -- Factor contributions sum up to the risk score (per its DDL comment)
    ('strategy.strategy_risk_factors', 'strategy.strategy_risks',  'computed_from', 'for_risk_contribution'),
    -- Period lenses aggregate the daily portfolio state
    ('strategy.strategy_risk_period',  'strategy.strategy_daily',  'computed_from', 'for_pnl_periods'),
    -- Trials belong to their training run
    ('strategy.training_trials',       'strategy.training_runs',   'anchored_by', 'for_run_id'),
    -- the two convenience views join their source tables (08_views.sql)
    ('strategy.v_trade_decision_full', 'strategy.strategy_identity', 'computed_from', 'for_identity'),
    ('strategy.v_trade_decision_full', 'strategy.strategy_results',  'computed_from', 'for_results'),
    ('strategy.v_trade_decision_full', 'strategy.trade_decision',    'computed_from', 'for_decisions'),
    ('strategy.v_strategy_risk_full',  'strategy.strategy_identity', 'computed_from', 'for_identity'),
    ('strategy.v_strategy_risk_full',  'strategy.strategy_results',  'computed_from', 'for_results'),
    ('strategy.v_strategy_risk_full',  'strategy.strategy_risks',    'computed_from', 'for_risks')
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
