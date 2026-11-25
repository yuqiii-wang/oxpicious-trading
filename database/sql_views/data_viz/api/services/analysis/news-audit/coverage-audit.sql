-- Coverage-audit rows for the News Audit page's COVERAGE view: the most
-- recent N trading days of text.news_coverage_audit (the daily per-
-- industry coverage grain; '000001' = the whole-market rollup row).
-- serves: GET /api/analysis/news-audit/coverage
-- args:
--   limit_days: value int — number of most recent distinct trading days
WITH recent AS (
    SELECT DISTINCT date
    FROM text.news_coverage_audit
    ORDER BY date DESC
    LIMIT {{limit_days}}::int
)
SELECT c.date,
       c.industry_id,
       c.n_articles,
       c.n_digested,
       c.n_fake,
       c.n_industries
FROM text.news_coverage_audit c
JOIN recent r USING (date)
ORDER BY c.date DESC, c.n_articles DESC, c.industry_id
