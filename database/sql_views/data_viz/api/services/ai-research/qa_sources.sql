-- AI research QA detail's 全部来源 list — the merged news group joined back
-- to its articles (each carries a 4000-char content preview).
-- Serves GET /api/ai/research-qa (the sources block).
-- args:
--   qa_id: value bigint — the research QA row whose news group to list
SELECT n.news_id, n.title, n.source, n.url,
           LEFT(n.content, 4000) AS content,
           (n.date)::text AS date
      FROM text.news_group_items gi
      JOIN text.news n ON n.news_id = gi.news_id
     WHERE gi.news_group_id = (
             SELECT news_group_id FROM llm.llm_qa_by_research
              WHERE qa_id = {{qa_id}})
     ORDER BY gi.news_id
