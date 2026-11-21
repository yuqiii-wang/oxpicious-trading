-- Distinct news origins (source, author) with their full-history seen-date
-- span — the sync source for the text.news_providers registry.
-- No args.
SELECT n.source,
       COALESCE(n.author, '') AS author,
       MIN(n.date)            AS first_seen_date,
       MAX(n.date)            AS last_seen_date,
       COUNT(*)               AS num_articles
FROM text.news n
GROUP BY n.source, COALESCE(n.author, '')
