-- Content-audit rows for the News Audit page: the most recent N windows
-- per industry, with the keyword rollup columns.
-- serves: GET /api/analysis/news-audit/content
-- args:
--   limit_windows: value int — number of most recent distinct window starts
--   industry_id: value text — industry filter; '' = all industries
WITH recent AS (
    SELECT DISTINCT window_start
    FROM text.news_content_audit
    ORDER BY window_start DESC
    LIMIT {{limit_windows}}::int
)
SELECT c.window_start,
       c.window_end,
       c.industry_id,
       c.is_complete,
       c.num_items,
       c.num_digested,
       c.num_fake,
       c.fake_pct,
       c.avg_sentiment,
       c.distinct_keywords,
       c.keyword_mentions,
       c.articles_with_keywords,
       c.computed_at
FROM text.news_content_audit c
JOIN recent r USING (window_start)
WHERE ({{industry_id}}::text = '' OR c.industry_id = {{industry_id}}::text)
ORDER BY c.window_start DESC, c.num_items DESC, c.industry_id
