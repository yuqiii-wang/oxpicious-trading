-- News top authors for the author filter chips — scoped by everything
-- EXCEPT the author itself (selecting an author narrows the other chips,
-- not this list). Ordered by the curated static.news_prestige rank —
-- the author's exact (provider, author) row when one exists, else the
-- provider-level row (author='') — then by count. Serves
-- GET /api/news/authors.
-- args:
--   where_clause: fragment — the full predicate "<scope conditions> AND
--     n.author IS NOT NULL" (caller-built; may carry params)
SELECT n.author, COUNT(*)::text AS n, COALESCE(pa.prestige, pp.prestige) AS prestige
  FROM text.news n
  LEFT JOIN static.news_prestige pa ON pa.provider = n.source AND pa.author = n.author
  LEFT JOIN static.news_prestige pp ON pp.provider = n.source AND pp.author = ''
 WHERE {{where_clause}}
 GROUP BY n.author, pa.prestige, pp.prestige
 ORDER BY COALESCE((pa.prestige->>'rank')::int, (pp.prestige->>'rank')::int) NULLS LAST,
          COUNT(*) DESC, n.author ASC
 LIMIT 60
