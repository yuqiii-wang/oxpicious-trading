-- ============================================================================
--  Knowledge-graph seed — namespace 0 (DB semantics): the debt / money-rate
--  chain (stats.debt_identity + the PBoC and market rate tables) and the
--  PBoC open-market announcements. Conventions:
--  .agents/skills/kg-db-semantics. DDL: kg_entities.sql + kg_edges.sql.
--
--  Idempotent — entities upsert on uq_kg_entities_ns_type_name; edges
--  insert only when the (relation, sub_relation, entity_ids) triple is
--  absent. Also carries stats.v_debt_daily's edges (the view entity is
--  seeded in 03, its member entities here).
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
    -- Debt sub-tables FK debt_identity on date
    ('stats.debt_omo',           'stats.debt_identity', 'anchored_by',   'for_date'),
    ('stats.debt_repo',          'stats.debt_identity', 'anchored_by',   'for_date'),
    ('stats.debt_outright_repo', 'stats.debt_identity', 'anchored_by',   'for_date'),
    ('stats.debt_mlf',           'stats.debt_identity', 'anchored_by',   'for_date'),
    ('stats.debt_shibor',        'stats.debt_identity', 'anchored_by',   'for_date'),
    ('stats.debt_treasury',      'stats.debt_identity', 'anchored_by',   'for_date'),
    ('stats.debt_lpr',           'stats.debt_identity', 'anchored_by',   'for_date'),
    -- v_debt_daily (entity seeded in 03) joins the debt chain per date
    ('stats.v_debt_daily', 'stats.debt_identity', 'computed_from', 'for_identity'),
    ('stats.v_debt_daily', 'stats.debt_treasury', 'computed_from', 'for_treasury_yield'),
    ('stats.v_debt_daily', 'stats.debt_shibor',   'computed_from', 'for_shibor'),
    ('stats.v_debt_daily', 'stats.debt_omo',      'computed_from', 'for_policy_ops')
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
