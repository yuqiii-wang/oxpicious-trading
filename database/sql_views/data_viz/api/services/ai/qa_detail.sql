-- AI QA detail — one llm.llm_qa row's full payload: the answer + context,
-- its per-card date (qa_date mapped to the latest trading day on or before
-- its Asia/Shanghai day via the MATERIALIZED td CTE), the news-group source
-- count and the mean ref sentiment (llm.llm_qa_refs joined to
-- text.news_digestions).
-- Serves GET /api/ai/qa.
-- args:
--   qa_id: value bigint — the QA row to fetch
WITH td AS MATERIALIZED (
       SELECT DISTINCT date FROM stats.industry_basic_stats
     ), q AS (
       SELECT q.*, (q.qa_date AT TIME ZONE 'Asia/Shanghai')::date AS qa_day
         FROM llm.llm_qa q
        WHERE q.qa_id = {{qa_id}}
     ), m AS (
       SELECT q.*, COALESCE(
                (SELECT MAX(td.date) FROM td WHERE td.date <= q.qa_day),
                q.qa_day) AS trade_day
         FROM q
     )
    SELECT m.qa_id, m.question, m.answer, m.context, m.industry_id,
           m.sector_id, m.category, m.llm_model, m.language,
           m.is_big_fluctuation, m.trade_day::text AS date,
           (m.updated_at AT TIME ZONE 'Asia/Shanghai')::text AS updated_at,
           (SELECT COUNT(*)::int FROM text.news_group_items gi
             WHERE gi.news_group_id = m.news_group_id) AS n_sources,
           (SELECT ROUND(AVG(d.sentiment_level)::numeric, 2)::float
              FROM llm.llm_qa_refs r
              JOIN text.news_digestions d ON d.news_id = r.news_id
             WHERE r.qa_id = m.qa_id AND d.sentiment_level IS NOT NULL)
             AS avg_ref_sentiment
      FROM m
