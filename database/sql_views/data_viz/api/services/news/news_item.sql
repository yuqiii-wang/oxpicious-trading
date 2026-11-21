-- News feed item detail — one article's full row + its comment count
-- (the social-style feed's click-to-expand fetch) + its digestion row's
-- sentiment / category / fake verdict (NULL while not digested).
-- Serves GET /api/news/item.
-- args:
--   news_id: value bigint — the article to fetch
--   category_decode: fragment — label_dim decode of d.category
--     (labelDecodeSql('news_category', 'd.category'))
SELECT n.news_id, n.title, n.source, n.date::text AS date, n.url,
            n.author, n.industry_id, n.sector_id, n.votes, n.content,
            n.is_big_fluctuation,
            (SELECT COUNT(*)::int FROM text.news_comments c
              WHERE c.news_id = n.news_id) AS comment_count,
            d.sentiment_level,
            {{category_decode}} AS category,
            d.is_fake_news
       FROM text.news n
       LEFT JOIN text.news_digestions d ON d.news_id = n.news_id
      WHERE n.news_id = {{news_id}}
