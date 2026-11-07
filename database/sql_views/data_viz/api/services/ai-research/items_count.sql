-- AI research items — total scoped-row count for the feed pager (one page
-- of research Q&A rows is ai-research/items). Same trading-day mapping as
-- ai-research/calendar.
-- Serves GET /api/ai/research-items (the total).
-- args:
--   where_clause: fragment — the buildFilterSql scope/search conditions
--     joined " AND " (aliased to q.*; carries the caller's $n params)
--   date_clause: fragment — "WHERE trade_day = $n::date" / "WHERE
--     trade_day BETWEEN $n::date AND $n::date" / "" (caller-built; may
--     carry params)
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
SELECT COUNT(*)::text AS n
  FROM m
  {{date_clause}}
