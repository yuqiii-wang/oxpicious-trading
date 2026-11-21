-- Per-article frame for the 5-day provider/content audits: every text.news
-- row from from_date on, LEFT JOINed with its digestion (sentiment / fake
-- verdict) and COALESCEd to the audit grains (author '' = provider-level,
-- industry_id '' = untagged). Dates ride as epoch float8 (the cudf DB-read
-- convention — python-date columns poison frames).
-- args:
--   from_date: value date — earliest publish date to load (a window start)
SELECT n.news_id,
       EXTRACT(EPOCH FROM n.date)::float8 AS date_epoch,
       n.source,
       COALESCE(n.author, '')       AS author,
       COALESCE(n.industry_id, '')  AS industry_id,
       n.word_count::float8         AS word_count,
       d.sentiment_level::float8    AS sentiment_level,
       COALESCE(d.is_fake_news, false)::int4 AS is_fake,
       (d.news_id IS NOT NULL)::int4         AS digested
FROM text.news n
LEFT JOIN text.news_digestions d ON d.news_id = n.news_id
WHERE n.date >= {{from_date}}::date
