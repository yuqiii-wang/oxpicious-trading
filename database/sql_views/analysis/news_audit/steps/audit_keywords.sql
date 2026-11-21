-- Per-keyword-row frame for the 5-day content audit: text.news_keywords
-- joined to its article's publish date + industry (the content-audit grain)
-- from from_date on. count is the affirmative hit count written by the
-- builds.text KeywordTaxonomy extraction (negated readings are never
-- written).
-- args:
--   from_date: value date — earliest publish date to load (a window start)
SELECT k.news_id,
       EXTRACT(EPOCH FROM n.date)::float8 AS date_epoch,
       COALESCE(n.industry_id, '') AS industry_id,
       k.keyword,
       k.count::int8 AS kw_count
FROM text.news_keywords k
JOIN text.news n ON n.news_id = k.news_id
WHERE n.date >= {{from_date}}::date
