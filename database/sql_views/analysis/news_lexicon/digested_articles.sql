-- Digested articles still missing a lexicon score: the analysis.news_lexicon
-- incremental target set. After a --force truncate (scores table emptied)
-- the anti-join returns the whole corpus, so one template serves both modes.
-- Dates ride as epoch float8 (the cudf DB-read convention).
-- args: (none)
SELECT n.news_id,
       EXTRACT(EPOCH FROM n.date)::float8          AS date_epoch,
       COALESCE(n.industry_id, '')                 AS industry_id,
       n.source,
       d.sentiment_level::float8                   AS sentiment_level,
       COALESCE(n.title, '')                       AS title,
       COALESCE(n.content, '')                     AS content
FROM text.news n
JOIN text.news_digestions d ON d.news_id = n.news_id
WHERE d.sentiment_level IS NOT NULL
  AND NOT EXISTS (
      SELECT 1 FROM text.news_lexicon_scores s WHERE s.news_id = n.news_id)
