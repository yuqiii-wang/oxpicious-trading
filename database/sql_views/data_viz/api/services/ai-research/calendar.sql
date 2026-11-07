-- AI research calendar — per-day research Q&A counts for the date-event
-- strip's dots. Each llm.llm_qa_by_research row's qa_date is mapped to the
-- latest trading day on or before its Asia/Shanghai day (MATERIALIZED td
-- CTE — computed once, not per correlated reference).
-- Serves GET /api/ai/research-calendar.
-- args:
--   where_clause: fragment — the buildFilterSql scope/search conditions
--     joined " AND " (aliased to q.*; carries the caller's $n params)
WITH td AS MATERIALIZED (
  SELECT DISTINCT date FROM stats.industry_basic_stats
), q AS (
  SELECT q.*, (q.qa_date AT TIME ZONE 'Asia/Shanghai')::date AS qa_day
    FROM llm.llm_qa_by_research q
   WHERE {{where_clause}}
), m AS (
  SELECT q.*, COALESCE(
           (SELECT MAX(td.date) FROM td WHERE td.date <= q.qa_day),
           q.qa_day) AS trade_day
    FROM q
)
SELECT trade_day::text AS date, COUNT(*)::text AS count
  FROM m
 GROUP BY trade_day
 ORDER BY trade_day
