-- AI ask-history detail — one llm.llm_qa_by_ask row's full payload: the
-- question + answer (or error tail), the ask's trading-day mapping (ask_date
-- bucketed to Asia/Shanghai days, then the latest trading day on or before
-- it via the MATERIALIZED td CTE), the 1:1 context row's chart/page/scope
-- columns and window bounds, the derived-keyword count and the image count.
-- Serves GET /api/ai/ask-qa.
-- args:
--   ask_id: value bigint — the ask row to fetch
WITH td AS MATERIALIZED (
       SELECT DISTINCT date FROM stats.industry_basic_stats
     ), q AS (
       SELECT a.*, (a.ask_date AT TIME ZONE 'Asia/Shanghai')::date AS ask_day,
              c.chart_kind, c.chart_title, c.page,
              c.industry_id AS ctx_industry_id, c.sector_id AS ctx_sector_id,
              c.window_start, c.window_end, c.n_images
         FROM llm.llm_qa_by_ask a
         LEFT JOIN llm.llm_qa_ask_context c ON c.ask_id = a.ask_id
        WHERE a.ask_id = {{ask_id}}
     ), m AS (
       SELECT q.*, COALESCE(
                (SELECT MAX(td.date) FROM td WHERE td.date <= q.ask_day),
                q.ask_day) AS trade_day
         FROM q
     )
    SELECT m.ask_id, m.question, m.answer, m.error_tail, m.status,
           m.online_search, m.search_query, m.provider, m.llm_model,
           m.language, m.code, m.product,
           m.trade_day::text AS date,
           (m.updated_at AT TIME ZONE 'Asia/Shanghai')::text AS updated_at,
           COALESCE(m.n_images, 0)::int AS n_images,
           (SELECT COUNT(*)::int FROM llm.llm_qa_keywords_by_ask k
             WHERE k.ask_id = m.ask_id) AS n_keywords,
           m.chart_kind, m.chart_title, m.page,
           m.ctx_industry_id AS industry_id, m.ctx_sector_id AS sector_id,
           m.window_start, m.window_end
      FROM m
