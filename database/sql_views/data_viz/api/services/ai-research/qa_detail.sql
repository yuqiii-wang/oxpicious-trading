-- AI research QA detail — one llm.llm_qa_by_research row's full payload:
-- the summary answer + the five evaluation dimensions (intro /
-- optimistic_forecast / pessimistic_forecast / risks / capitalization) with
-- the persisted dim_refs blocks, the trading-day-mapped date (qa_date → the
-- latest trading day on or before its Asia/Shanghai day), the news-group
-- source count and the mean ref sentiment over the merged news group.
-- logic_label decodes from its static.label_dim code (domain research_logic)
-- at the SQL boundary.
-- Serves GET /api/ai/research-qa.
-- args:
--   qa_id: value bigint — the research QA row to fetch
--   logic_label_decode: fragment — labelDecodeSql("research_logic", "m.logic_label")
WITH td AS MATERIALIZED (
       SELECT DISTINCT date FROM stats.industry_basic_stats
     ), q AS (
       SELECT q.*, (q.qa_date AT TIME ZONE 'Asia/Shanghai')::date AS qa_day
         FROM llm.llm_qa_by_research q
        WHERE q.qa_id = {{qa_id}}
     ), m AS (
       SELECT q.*, COALESCE(
                (SELECT MAX(td.date) FROM td WHERE td.date <= q.qa_day),
                q.qa_day) AS trade_day
         FROM q
     )
    SELECT m.qa_id, m.question, m.answer, m.context, m.intro,
           m.optimistic_forecast, m.pessimistic_forecast,
           m.risks, m.capitalization, m.dim_refs,
           m.industry_id, m.sector_id, m.category,
           {{logic_label_decode}} AS logic_label,
           m.llm_model, m.language, m.trade_day::text AS date,
           (m.updated_at AT TIME ZONE 'Asia/Shanghai')::text AS updated_at,
           (SELECT COUNT(*)::int FROM text.news_group_items gi
             WHERE gi.news_group_id = m.news_group_id) AS n_sources,
           (SELECT ROUND(AVG(d.sentiment_level)::numeric, 2)::float
              FROM text.news_group_items gi
              JOIN text.news_digestions d ON d.news_id = gi.news_id
             WHERE gi.news_group_id = m.news_group_id
               AND d.sentiment_level IS NOT NULL)
             AS avg_ref_sentiment
      FROM m
