-- AI calendar bounds — the min/max trade days of the scoped QA rows (the
-- date-event strip's axis). Same trading-day mapping as ai/calendar.
-- Serves GET /api/ai/calendar (the min_date/max_date block).
-- args:
--   where_clause: fragment — the buildFilterSql scope/search conditions
--     joined " AND " (aliased to q.*; carries the caller's $n params)
WITH td AS MATERIALIZED (
  SELECT DISTINCT date FROM stats.industry_basic_stats
), q AS (
  SELECT q.*, (q.qa_date AT TIME ZONE 'Asia/Shanghai')::date AS qa_day
    FROM llm.llm_qa q
   WHERE {{where_clause}}
), m AS (
  SELECT q.*, COALESCE(
           (SELECT MAX(td.date) FROM td WHERE td.date <= q.qa_day),
           q.qa_day) AS trade_day
    FROM q
)
SELECT MIN(trade_day)::text AS min_date, MAX(trade_day)::text AS max_date
  FROM m
