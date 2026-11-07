-- News count by industry_id — the nav tree's chip counts (uncatalogued
-- ids surface as the Unclassified bucket; grouping includes the '' bucket
-- for untagged rows).
-- Serves GET /api/news/themes and GET /api/news/strategy-themes.
-- args:
--   where_clause: fragment — "WHERE source = $n [AND author = $n]" / ""
--     (caller-built; may carry params)
SELECT COALESCE(industry_id, '') AS industry_id, COUNT(*)::text AS n
  FROM text.news
  {{where_clause}}
 GROUP BY 1
