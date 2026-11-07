-- AI research QA detail — the dimension_refs expansion's article fetch:
-- the deduped news_ids referenced by the row's dim_refs blocks, one join
-- over every referenced article.
-- Serves GET /api/ai/research-qa (the per-dimension ref boxes).
-- args:
--   news_ids: value bigint[] — the referenced article ids
SELECT news_id, title, source, url, LEFT(content, 4000) AS content,
                  (date)::text AS date
             FROM text.news WHERE news_id = ANY({{news_ids}}::bigint[])
