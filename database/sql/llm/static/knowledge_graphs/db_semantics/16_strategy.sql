-- ============================================================================
--  Knowledge-graph seed — namespace 0 (DB semantics): the strategy
--  backtest/training chain — run identity, results, daily portfolio state,
--  trade decisions, risk metrics and their factor/period lenses, the
--  per-security algo parameter overrides, and the training process log.
--  Conventions: .agents/skills/kg-db-semantics. DDL: 13_kg_hypergraph.sql.
--
--  Idempotent — entities upsert on uq_kg_entities_ns_type_name; edges
--  insert only when the (relation, sub_relation, entity_ids) triple is
--  absent.
-- ============================================================================

INSERT INTO llm.kg_entities (namespace, name, entity_type, tags, description) VALUES
    (0, 'strategy.strategy_identity',   'table', ARRAY['strategy', '策略', 'backtest', '回测', 'identity', '标识', 'reference', '参考'],
     'One strategy execution on one security — the run header every result table keys on.'),
    (0, 'strategy.strategy_results',    'table', ARRAY['strategy', '策略', 'results', '结果', 'pnl', '盈亏'],
     'Each strategy run''s outcome — total cost, the price-normalization anchor and the profit-and-loss summary.'),
    (0, 'strategy.strategy_daily',      'table', ARRAY['strategy', '策略', 'daily state', '每日持仓', 'portfolio', '持仓', 'unrealized pnl', '浮动盈亏'],
     'Each strategy run''s daily portfolio state — remaining holdings and unrealized P&L per day.'),
    (0, 'strategy.trade_decision',      'table', ARRAY['trade decision', '交易决策', 'buy sell', '买卖', 'positions', '仓位'],
     'Each strategy run''s ordered per-code trade decisions in normalized price units — the long-only buy/sell sequence.'),
    (0, 'strategy.strategy_risks',      'table', ARRAY['risk', '风险', 'drawdown', '回撤', 'concentration', '集中度', 'risk score', '风险分'],
     'Each strategy run''s risk metrics — P&L concentration, exponential risk score, drawdowns and amplified losses.'),
    (0, 'strategy.strategy_risk_factors', 'table', ARRAY['risk factors', '风险因子', 'contribution', '贡献', 'risk score', '风险分'],
     'The per-factor contributions behind each risk score.'),
    (0, 'strategy.strategy_risk_period', 'table', ARRAY['risk', '风险', 'periodic', '分期', 'monthly', '月度', 'concentration', '集中度'],
     'Per-period gain/loss aggregation — yearly, seasonal and monthly risk lenses with top trades and concentration flags.'),
    (0, 'strategy.algo_configs',        'table', ARRAY['algo configs', '算法参数', 'parameters', '参数', 'overrides', '覆盖', 'reference', '参考'],
     'Per-security, per-strategy parameter overrides for a date range — what a backtest loads on top of the algo defaults.'),
    (0, 'strategy.training_runs',       'table', ARRAY['training', '训练', 'parameter search', '参数搜索', 'process log', '过程日志'],
     'One strategy-training process — the log header of a parameter search for one security and strategy.'),
    (0, 'strategy.training_trials',     'table', ARRAY['training', '训练', 'trials', '试验', 'grid search', '网格搜索', 'metrics', '指标'],
     'One training run''s per-candidate trials — each grid point''s parameters and backtest metrics.')
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
    ('strategy.training_trials',       'strategy.training_runs',   'anchored_by', 'for_run_id')
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
