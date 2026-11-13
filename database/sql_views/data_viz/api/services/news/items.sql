-- News items — one page of articles for the feed cards (comment_count
-- rides a subquery over text.news_comments; snippet is the content's head;
-- sentiment_level is the article's text.news_digestions row — NULL while
-- the article is not yet digested). Serves GET /api/news/items.
-- args:
--   where_clause: fragment — "WHERE <scope/search/date conditions>" / ""
--     (caller-built; may carry params)
--   limit: fragment — clamped page size int (caller-built literal)
--   offset: fragment — clamped page offset int (caller-built literal)
SELECT n.news_id, n.title, n.source, n.date::text AS date, n.url,
       n.author, n.industry_id, n.sector_id, n.votes, n.is_big_fluctuation,
       (SELECT COUNT(*)::int FROM text.news_comments c
         WHERE c.news_id = n.news_id) AS comment_count,
       d.sentiment_level,
       -- LEFT(NULL, n) is NULL: no guard needed for the missing content
       LEFT(n.content, 200) AS snippet
  FROM text.news n
  LEFT JOIN text.news_digestions d ON d.news_id = n.news_id
  {{where_clause}}
 ORDER BY n.date DESC, n.votes DESC NULLS LAST, n.source ASC, n.news_id ASC
 LIMIT {{limit}} OFFSET {{offset}}
