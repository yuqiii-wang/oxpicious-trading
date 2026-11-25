-- Provider-audit rows for the News Audit page: the most recent N windows,
-- each origin joined with its registry row (news_type media type +
-- is_blocked + seen-date span).
-- serves: GET /api/analysis/news-audit/providers
-- args:
--   limit_windows: value int — number of most recent distinct window starts
--   source: value text — provider filter; '' = all providers
--   news_type_decode: fragment — labelDecodeSql("news_type", "pr.news_type")
WITH recent AS (
    SELECT DISTINCT window_start
    FROM text.news_provider_audit
    ORDER BY window_start DESC
    LIMIT {{limit_windows}}::int
)
SELECT p.window_start,
       p.window_end,
       p.source,
       p.author,
       p.is_complete,
       p.num_items,
       p.num_digested,
       p.num_fake,
       p.fake_pct,
       p.avg_sentiment,
       p.avg_word_count,
       p.num_industries,
       p.computed_at,
       pr.news_type,
       {{news_type_decode}} AS news_type_label,
       pr.is_blocked,
       pr.first_seen_date,
       pr.last_seen_date
FROM text.news_provider_audit p
JOIN recent r USING (window_start)
LEFT JOIN text.news_providers pr
       ON pr.source = p.source AND pr.author = p.author
WHERE ({{source}}::text = '' OR p.source = {{source}}::text)
ORDER BY p.window_start DESC, p.num_items DESC, p.source, p.author
