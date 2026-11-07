-- News feed item comments — the article's flat comment rows (the service
-- rebuilds the reply threading client-side).
-- Serves GET /api/news/comments.
-- args:
--   news_id: value bigint — the article whose comments to list
SELECT c.comment_id, c.parent_comment_id, c.author, c.content,
            c.date::text AS date, c.votes, c.is_reply
       FROM text.news_comments c
      WHERE c.news_id = {{news_id}}
      ORDER BY c.date ASC, c.comment_id ASC
