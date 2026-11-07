-- ============================================================================
--  Knowledge-graph seed — namespace 0 (DB semantics): the signal tier and
--  its live counterpart — the constant rule cells, the replayed history
--  events, the dated performance audits, the portfolio tier over them, the
--  schema registries, and the intraday live stores. Conventions:
--  .agents/skills/kg-db-semantics. DDL: 13_kg_hypergraph.sql.
--
--  Idempotent — entities upsert on uq_kg_entities_ns_type_name; edges
--  insert only when the (relation, sub_relation, entity_ids) triple is
--  absent.
-- ============================================================================

INSERT INTO llm.kg_entities (namespace, name, entity_type, tags, description) VALUES
    (0, 'analysis_signals.signal_rules',       'table', ARRAY['signal rules', '信号规则', 'thresholds', '阈值', 'rule cells', '规则单元', 'reference', '参考'],
     'The constant signal-rule cells — one row per code, config, trend, regime and action whose thresholds the history replay and live tier test against.'),
    (0, 'analysis_signals.history_signals',    'table', ARRAY['history signals', '历史信号', 'trigger', '触发', 'signal events', '信号事件'],
     'The replayed history signal events — each rule cell''s qualifying trigger days with the day''s evidence and confidence.'),
    (0, 'analysis_signals.history_signal_performance', 'table', ARRAY['performance', '绩效', 'audit', '审计', 'confidence', '置信度'],
     'Per rule cell''s dated performance snapshots — the measured evidence of each cell''s triggers as of each audit date.'),
    (0, 'analysis_signals.portfolio_signal_rules', 'table', ARRAY['portfolio rules', '组合规则', 'hedge pair', '对冲组合', 'rule cells', '规则单元'],
     'Portfolio-tier rules combining member signal cells into coordinated portfolios — hedge pairs and their construction spec.'),
    (0, 'analysis_signals.portfolio_history_signals', 'table', ARRAY['portfolio signals', '组合信号', 'coordinated events', '协同事件', 'member legs', '成员腿'],
     'The replayed portfolio events — coordinated member-cell actions recorded as one event with its member legs.'),
    (0, 'analysis_signals.history_portfolio_signal_performance', 'table', ARRAY['portfolio performance', '组合绩效', 'audit', '审计', 'benefit', '收益结构'],
     'Per portfolio rule''s dated audit snapshots — the pooled member performance and the portfolio''s structure benefit.'),
    (0, 'analysis_signals.analysis_cache', 'table', ARRAY['cache', '缓存', 'intermediate results', '中间结果', 'boost derivation', '推导缓存'],
     'The generic intermediate-result cache of the signal analyses — self-describing cache payloads per kind, signal type and security type, regenerable by re-running their owning analysis.'),
    (0, 'analysis.analysis_identity', 'table', ARRAY['analysis registry', '分析注册表', 'identity', '标识', 'reference', '参考'],
     'The registry of analyses stored in the analysis schema — one row per study.'),
    (0, 'live.live_identity',         'table', ARRAY['live registry', '实时注册表', 'identity', '标识', 'reference', '参考'],
     'The registry of live pipelines stored in the live schema — one row per pipeline.'),
    (0, 'live.live_signals',          'table', ARRAY['live signals', '实时信号', 'breach', '突破', 'intraday', '盘中', 'threshold', '阈值'],
     'Intraday threshold-breach records — each tick at which a monitored value crossed its configured signal level.'),
    (0, 'live.sec_alloc_live_attribution', 'table', ARRAY['attribution', '归因', 'live', '实时', 'benchmark', '基准', 'tick', '分时'],
     'Live per-tick member attribution against a benchmark index — member and benchmark moves cached per 5-minute tick.'),
    (0, 'static.signal_rules_docs',   'table', ARRAY['signal rules', '信号规则', 'knowledge docs', '知识文档', 'family guide', '家族说明', 'reference', '参考'],
     'The signal-family knowledge documents — one business document per signal family, served with every rule cell.'),
    (0, 'static.portfolio_rules_docs','table', ARRAY['portfolio rules', '组合规则', 'knowledge docs', '知识文档', 'reference', '参考'],
     'The portfolio-rule knowledge documents — one business document per portfolio type.')
ON CONFLICT (namespace, entity_type, name) DO UPDATE
    SET description = EXCLUDED.description,
        tags        = EXCLUDED.tags;

INSERT INTO llm.kg_edges (namespace, relation, sub_relation, entity_ids)
SELECT 0, e.relation, e.sub_relation, ARRAY[a.entity_id, b.entity_id]::BIGINT[]
FROM (VALUES
    -- History tier replays the constant rules; the performance audit
    -- measures the replayed events (per the DDL comments)
    ('analysis_signals.history_signals',             'analysis_signals.signal_rules', 'computed_from', 'for_replay'),
    ('analysis_signals.history_signal_performance',  'analysis_signals.history_signals', 'computed_from', 'for_measure'),
    -- Portfolio tier: rules reference member cells; portfolio events replay
    -- the portfolio rules over the member cells' events
    ('analysis_signals.portfolio_signal_rules', 'analysis_signals.signal_rules', 'computed_from', 'for_member_cells'),
    ('analysis_signals.portfolio_history_signals', 'analysis_signals.portfolio_signal_rules', 'computed_from', 'for_replay'),
    ('analysis_signals.portfolio_history_signals', 'analysis_signals.history_signals', 'computed_from', 'for_member_cells'),
    ('analysis_signals.history_portfolio_signal_performance', 'analysis_signals.portfolio_signal_rules', 'computed_from', 'for_member_cells'),
    ('analysis_signals.history_portfolio_signal_performance', 'analysis_signals.history_signal_performance', 'computed_from', 'for_member_measures'),
    -- Family knowledge documents joined at the read boundary
    ('analysis_signals.signal_rules',       'static.signal_rules_docs',    'documented_by', NULL),
    ('analysis_signals.portfolio_signal_rules', 'static.portfolio_rules_docs', 'documented_by', NULL),
    -- Live tier
    ('live.live_signals', 'analysis_signals.history_signals', 'similar_to', 'about_signal_trigger'),
    -- Live attribution reads the prev-day closes + shared weights at read
    -- time (per its DDL comment)
    ('live.sec_alloc_live_attribution', 'stats.index_basic_stats', 'computed_from', 'for_prev_close'),
    ('live.sec_alloc_live_attribution', 'stats.cross_stats',       'computed_from', 'for_shared_weights')
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
