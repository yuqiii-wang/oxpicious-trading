-- ============================================================================
--  Knowledge-graph seed — namespace 0 (DB semantics): the signal tier and
--  its live counterpart — the constant rule cells, the replayed history
--  events, the dated performance audits, the portfolio tier over them, the
--  schema registries, and the intraday live stores. Conventions:
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
    ('analysis_signals.history_signals', 'analysis_signals.signal_rules', 'computed_from', 'for_replay'),
    ('analysis_signals.history_signal_performance', 'analysis_signals.history_signals', 'computed_from', 'for_measure'),
    ('analysis_signals.portfolio_signal_rules', 'analysis_signals.signal_rules', 'computed_from', 'for_member_cells'),
    ('analysis_signals.portfolio_history_signals', 'analysis_signals.portfolio_signal_rules', 'computed_from', 'for_replay'),
    ('analysis_signals.portfolio_history_signals', 'analysis_signals.history_signals', 'computed_from', 'for_member_cells'),
    ('analysis_signals.history_portfolio_signal_performance', 'analysis_signals.portfolio_signal_rules', 'computed_from', 'for_member_cells'),
    ('analysis_signals.history_portfolio_signal_performance', 'analysis_signals.history_signal_performance', 'computed_from', 'for_member_measures'),
    ('live.live_signals', 'analysis_signals.history_signals', 'similar_to', 'about_signal_trigger'),
    ('live.sec_alloc_live_attribution', 'stats.cross_stats', 'computed_from', 'for_shared_weights'),
    ('live.sec_alloc_live_attribution', 'stats.index_basic_stats', 'computed_from', 'for_prev_close')
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
