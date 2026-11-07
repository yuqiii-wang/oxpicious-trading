-- AI ask-history calendar bounds — the min/max trade days of the scoped
-- ask rows (the date-event strip's axis). Same trading-day mapping as
-- ai-ask-history/calendar.
-- Serves GET /api/ai/ask-calendar (the min_date/max_date block).
-- args:
--   where_clause: fragment — the buildAskFilterSql scope/search conditions
--     joined " AND " (aliased to q.*/c.*; carries the caller's $n params)
WITH td AS MATERIALIZED (
  SELECT DISTINCT date FROM stats.industry_basic_stats
), q AS (
  SELECT q.*, (q.ask_date AT TIME ZONE 'Asia/Shanghai')::date AS ask_day,
         c.industry_id AS ctx_industry_id, c.sector_id AS ctx_sector_id,
         c.n_images
    FROM llm.llm_qa_by_ask q
    LEFT JOIN llm.llm_qa_ask_context c ON c.ask_id = q.ask_id
   WHERE {{where_clause}}
), m AS (
  SELECT q.*, COALESCE(
           (SELECT MAX(td.date) FROM td WHERE td.date <= q.ask_day),
           q.ask_day) AS trade_day
    FROM q
)
SELECT MIN(trade_day)::text AS min_date, MAX(trade_day)::text AS max_date
  FROM m
