-- News top authors for the author filter chips — scoped by everything
-- EXCEPT the author itself (selecting an author narrows the other chips,
-- not this list).
-- Serves GET /api/news/authors.
-- args:
--   where_clause: fragment — the full predicate "<scope conditions> AND
--     n.author IS NOT NULL" (caller-built; may carry params)
SELECT n.author, COUNT(*)::text AS n
  FROM text.news n
 WHERE {{where_clause}}
 GROUP BY n.author
 ORDER BY COUNT(*) DESC, n.author ASC
 LIMIT 30
