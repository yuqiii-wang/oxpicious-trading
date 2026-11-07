-- ============================================================================
--  Knowledge-graph seed — namespace 0 (DB semantics): the text corpus (news
--  articles, comments, keywords, LLM digestions, reference groups, the
--  keyword-audit matview, the search lexicon), the static reference tables
--  (keyword map, label-code contract, authored docs), and the insight
--  registries. Conventions: .agents/skills/kg-db-semantics. DDL:
--  13_kg_hypergraph.sql.
--
--  Idempotent — entities upsert on uq_kg_entities_ns_type_name; edges
--  insert only when the (relation, sub_relation, entity_ids) triple is
--  absent.
-- ============================================================================

INSERT INTO llm.kg_entities (namespace, name, entity_type, tags, description) VALUES
    (0, 'text.news',                    'table', ARRAY['news', '新闻', 'corpus', '语料', 'articles', '文章', 'sentiment', '情绪', 'full-text search', '全文检索'],
     'The news corpus — one raw article per source per day, with industry tags and a full-text search vector.'),
    (0, 'text.news_comments',           'table', ARRAY['comments', '评论', 'news', '新闻', 'discussion', '讨论'],
     'Reader comments on corpus articles — the discussion thread around each piece.'),
    (0, 'text.news_keywords',           'table', ARRAY['keywords', '关键词', 'idf', 'search', '检索', 'audit', '审计'],
     'Per-article keyword frequencies with corpus-wide document frequencies and idf — the search and audit vocabulary in action.'),
    (0, 'text.news_digestions',         'table', ARRAY['digestion', '摘要', 'llm', 'sentiment', '情绪', 'news', '新闻'],
     'Per-article LLM digestion — a summary and a sentiment score for each piece.'),
    (0, 'text.news_embeddings',         'table', ARRAY['embeddings', '向量', 'semantic search', '语义检索'],
     'Per-article content embeddings — reserved for the semantic-search pipeline.'),
    (0, 'text.news_groups',             'table', ARRAY['news groups', '新闻组', 'reference set', '引用集合'],
     'Reference sets of articles cited together — e.g. the sources behind one AI answer.'),
    (0, 'text.news_group_items',        'table', ARRAY['news groups', '新闻组', 'membership', '成员'],
     'Which articles belong to which reference group.'),
    (0, 'text.news_keyword_audit',      'view',  ARRAY['keyword audit', '关键词审计', 'daily rollup', '按日汇总', 'category', '类别'],
     'The daily keyword audit rollup — how many articles touched each category''s keywords each day.'),
    (0, 'text.search_lexicon_df',       'table', ARRAY['search', '检索', 'lexicon', '词库', 'full-text search', '全文检索', 'reference', '参考'],
     'The overly-common lexemes per searchable table — the words whose ubiquity switches full-text search to date-ordered ranking.'),
    (0, 'static.keyword_maps',          'table', ARRAY['keyword map', '关键词映射', 'category', '类别', 'audit vocabulary', '审计词表', 'reference', '参考'],
     'The keyword-to-category map — which literal keyword''s occurrence evidences an industry, market-state or macro theme in an article.'),
    (0, 'static.label_dim',             'table', ARRAY['label codes', '标签编码', 'vocabulary', '词表', 'reference', '参考'],
     'The closed vocabulary mapping labels to SMALLINT codes — the decode contract for every coded label column.'),
    (0, 'static.docs',                  'table', ARRAY['descriptions', '描述文案', 'docs', '文档', 'reference', '参考'],
     'The registry of authored explanation texts for the platform''s charts, popovers and columns — its self-descriptions.'),
    (0, 'analysis_insights.insight_templates', 'table', ARRAY['insights', '洞察', 'templates', '模板', 'metrics', '指标'],
     'One insight template per analysis metric of particular interest — the ask-time value specs of the insight tier (analysis_insights copy).'),
    (0, 'analysis_insight_metrics.insight_templates', 'table', ARRAY['insights', '洞察', 'templates', '模板', 'metrics', '指标'],
     'One insight template per analysis metric of particular interest — the ask-time value specs of the insight tier.'),
    (0, 'analysis_insight_metrics.insight_page_docs', 'table', ARRAY['insights', '洞察', 'page docs', '页面说明', 'keywords', '关键词'],
     'Per analysis page — the page''s ask description and its business keywords.')
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
    ('text.news_keyword_audit', 'static.keyword_maps', 'computed_from', 'for_category_map')
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
