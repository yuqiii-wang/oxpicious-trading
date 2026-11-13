-- ============================================================================
--  Knowledge-graph seed — namespace 0 (DB semantics): the text corpus (news
--  articles, comments, keywords, LLM digestions, reference groups, the
--  keyword-audit matview, the search lexicon), the static reference tables
--  (keyword map, label-code contract, authored docs), and the insight
--  registries. Conventions: .agents/skills/kg-db-semantics. DDL:
--  kg_entities.sql + kg_edges.sql.
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

INSERT INTO llm.kg_entities (namespace, name, entity_type, tags, description) VALUES
    (0, 'text.news_keyword_audit',      'view',  ARRAY['keyword audit', '关键词审计', 'daily rollup', '按日汇总', 'category', '类别'],
     'The daily keyword audit rollup — how many articles touched each category''s keywords each day.')
ON CONFLICT (namespace, entity_type, name) DO UPDATE
    SET description = EXCLUDED.description,
        tags        = EXCLUDED.tags;

INSERT INTO llm.kg_edges (namespace, relation, sub_relation, entity_ids)
SELECT 0, e.relation, e.sub_relation, ARRAY[a.entity_id, b.entity_id]::BIGINT[]
FROM (VALUES
    -- Article-keyed tables FK text.news; the junction FKs both sides
    ('text.news_comments',     'text.news',          'anchored_by', 'for_news_id'),
    ('text.news_keywords',     'text.news',          'anchored_by', 'for_news_id'),
    ('text.news_digestions',   'text.news',          'anchored_by', 'for_news_id'),
    ('text.news_embeddings',   'text.news',          'anchored_by', 'for_news_id'),
    ('text.news_group_items',  'text.news_groups',   'anchored_by', 'for_group_id'),
    ('text.news_group_items',  'text.news',          'anchored_by', 'for_news_id'),
    -- The audit matview rolls keyword counts through the category map
    ('text.news_keyword_audit', 'text.news_keywords',  'computed_from', 'for_keyword_counts'),
    ('text.news_keyword_audit', 'static.keyword_maps', 'computed_from', 'for_category_map'),
    -- The rules docs tables document their rule stores (2026-11-09: the
    -- edges predate this seed's restructure — folded in here; the
    -- signals docs read through static.signal_rules_docs /
    -- static.portfolio_rules_docs, referenced by the trading-signals
    -- services and the signal_rules_docs corpus rows)
    ('analysis_signals.signal_rules',         'static.signal_rules_docs',        'documented_by', NULL),
    ('analysis_signals.portfolio_signal_rules', 'static.portfolio_rules_docs',   'documented_by', NULL)
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