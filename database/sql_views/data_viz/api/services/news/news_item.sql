-- News feed item detail — one article's full row + its comment count
-- (the social-style feed's click-to-expand fetch).
-- Serves GET /api/news/item.
-- args:
--   news_id: value bigint — the article to fetch
SELECT n.news_id, n.title, n.source, n.date::text AS date, n.url,
            n.author, n.industry_id, n.sector_id, n.votes, n.content,
            n.is_big_fluctuation,
            (SELECT COUNT(*)::int FROM text.news_comments c
              WHERE c.news_id = n.news_id) AS comment_count
       FROM text.news n
      WHERE n.news_id = {{news_id}}
