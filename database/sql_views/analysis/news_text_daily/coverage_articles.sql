-- Article COVERAGE frame for the daily news-text metrics: every text.news
-- row from from_date on (digested or not — n_articles is the raw coverage
-- basis the abnormal-flow flag reads), fake verdicts included.
-- Dates ride as epoch float8 (the cudf DB-read convention).
-- args:
--   from_date: value date — earliest publish date to load (a warmup bound)
SELECT n.news_id,
       EXTRACT(EPOCH FROM n.date)::float8          AS date_epoch,
       COALESCE(n.industry_id, '')                 AS industry_id
FROM text.news n
WHERE n.date >= {{from_date}}::date
