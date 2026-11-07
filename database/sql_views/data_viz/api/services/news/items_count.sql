-- News items — total scoped-article count for the feed pager (one page of
-- articles is news/items).
-- Serves GET /api/news/items (the total).
-- args:
--   where_clause: fragment — "WHERE <scope/search/date conditions>" / ""
--     (caller-built; may carry params)
SELECT COUNT(*)::text AS n
  FROM text.news n
  {{where_clause}}
