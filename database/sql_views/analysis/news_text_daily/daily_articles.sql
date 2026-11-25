-- Per-article frame for the daily news-text metrics: every digested,
-- non-fake-scored article from from_date on, with its LLM digestion score
-- and CFSD lexicon score joined. Rows without a lexicon score yet pass
-- through (lex_score NULL — the blend falls back to the LLM side alone).
-- Fake-verdict articles are excluded here (weight zero by design); the
-- COALESCEd industry_id / sector_id '' keeps untagged rows groupable
-- (sector_id is the denormalized L1 parent of industry_id — set iff
-- industry_id is set; the '' bucket drops out of the sector scope).
-- Dates ride as epoch float8 (the cudf DB-read convention).
-- args:
--   from_date: value date — earliest publish date to load (a warmup bound)
SELECT n.news_id,
       EXTRACT(EPOCH FROM n.date)::float8           AS date_epoch,
       COALESCE(n.industry_id, '')                  AS industry_id,
       COALESCE(n.sector_id, '')                    AS sector_id,
       n.source,
       d.sentiment_level::float8                    AS sentiment_level,
       s.lex_score::float8                          AS lex_score,
       COALESCE(s.disagrees, false)::int4           AS disagrees
FROM text.news n
JOIN text.news_digestions d ON d.news_id = n.news_id
LEFT JOIN text.news_lexicon_scores s ON s.news_id = n.news_id
WHERE n.date >= {{from_date}}::date
  AND d.sentiment_level IS NOT NULL
  AND COALESCE(d.is_fake_news, false) = false
