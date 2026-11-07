-- News calendar — per-day article counts for the date bar's dots,
-- co-filtered by industry scope + keyword search + optional date window so
-- the bar reacts to the classification nav.
-- Serves GET /api/news/calendar.
-- args:
--   where_clause: fragment — "WHERE <scope/search conditions> [AND
--     n.date BETWEEN $n AND $n]" / "" (caller-built; may carry params)
SELECT n.date::text AS date, COUNT(*)::text AS count
  FROM text.news n
  {{where_clause}}
 GROUP BY n.date
 ORDER BY n.date
