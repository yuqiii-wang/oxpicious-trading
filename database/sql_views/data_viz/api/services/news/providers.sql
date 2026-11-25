-- News providers for the provider filter chips — every DISTINCT
-- text.news.source with its article count, ordered by the curated
-- static.news_prestige rank first (provider-level rows, author=''),
-- then by count — the LLM-query reference downloads (search-engine
-- media names) rank after the curated pipeline/portal providers.
-- prestige rides along for the UI chip tooltip. Scoped by everything
-- EXCEPT the source itself (selecting a provider narrows the other
-- chips, not this list). Serves GET /api/news/providers.
-- args:
--   where_clause: fragment — "WHERE <scope/search/author conditions>" /
--     "" (caller-built; may carry params)
SELECT n.source AS provider, COUNT(*)::text AS n, p.prestige
  FROM text.news n
  LEFT JOIN static.news_prestige p ON p.provider = n.source AND p.author = ''
  {{where_clause}}
 GROUP BY n.source, p.prestige
 ORDER BY (p.prestige->>'rank')::int NULLS LAST, COUNT(*) DESC, n.source ASC
