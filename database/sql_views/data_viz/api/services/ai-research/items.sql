-- AI research items — one page of research Q&A rows (the feed cards): the
-- summary is the snippet; logic_label is a static.label_dim SMALLINT code
-- decoded via the caller's labelDecodeSql fragment. Same trading-day
-- mapping as ai-research/calendar.
-- Serves GET /api/ai/research-items.
-- args:
--   where_clause: fragment — the buildFilterSql scope/search conditions
--     joined " AND " (aliased to q.*; carries the caller's $n params)
--   date_clause: fragment — "WHERE trade_day = $n::date" / "WHERE
--     trade_day BETWEEN $n::date AND $n::date" / "" (caller-built; may
--     carry params)
--   logic_label_decode: fragment — labelDecodeSql("research_logic",
--     "m.logic_label")
--   limit: fragment — clamped page size int (caller-built literal)
--   offset: fragment — clamped page offset int (caller-built literal)
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
SELECT m.qa_id, m.question, m.industry_id, m.sector_id, m.category,
       {{logic_label_decode}} AS logic_label,
       m.llm_model, m.language, m.trade_day::text AS date,
       (m.updated_at AT TIME ZONE 'Asia/Shanghai')::text AS updated_at,
       LEFT(m.answer, 240) AS answer_snippet,
       (SELECT COUNT(*)::int FROM text.news_group_items gi
         WHERE gi.news_group_id = m.news_group_id) AS n_sources
  FROM m
  {{date_clause}}
 ORDER BY trade_day DESC, qa_id DESC
 LIMIT {{limit}} OFFSET {{offset}}
