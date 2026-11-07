-- News calendar — the date bar's global bounds (the whole text.news
-- table's min/max date; the per-day counts query stays in the service —
-- its WHERE is assembled per filter combination).
-- Serves GET /api/news/calendar (the min_date/max_date tail).
-- args: none
SELECT MIN(date)::text AS min_date, MAX(date)::text AS max_date FROM text.news
