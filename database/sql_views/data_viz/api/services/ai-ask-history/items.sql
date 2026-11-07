-- AI ask-history items — one page of ask rows (the feed cards): failed
-- asks surface with their error tail as the snippet. Same trading-day
-- mapping as ai-ask-history/calendar.
-- Serves GET /api/ai/ask-items.
-- args:
--   where_clause: fragment — the buildAskFilterSql scope/search conditions
--     joined " AND " (aliased to q.*/c.*; carries the caller's $n params)
--   date_clause: fragment — "WHERE trade_day = $n::date" / "WHERE
--     trade_day BETWEEN $n::date AND $n::date" / "" (caller-built; may
--     carry params)
--   limit: fragment — clamped page size int (caller-built literal)
--   offset: fragment — clamped page offset int (caller-built literal)
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
SELECT m.ask_id, m.question, m.code, m.product, m.status,
       m.online_search, m.llm_model, m.language,
       m.trade_day::text AS date,
       (m.updated_at AT TIME ZONE 'Asia/Shanghai')::text AS updated_at,
       LEFT(COALESCE(m.answer, m.error_tail), 240) AS answer_snippet,
       COALESCE(m.n_images, 0)::int AS n_images,
       (SELECT COUNT(*)::int FROM llm.llm_qa_keywords_by_ask k
         WHERE k.ask_id = m.ask_id) AS n_keywords
  FROM m
  {{date_clause}}
 ORDER BY trade_day DESC, ask_id DESC
 LIMIT {{limit}} OFFSET {{offset}}
