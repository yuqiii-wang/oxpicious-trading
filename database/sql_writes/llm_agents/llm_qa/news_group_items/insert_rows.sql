-- SQL write action — text.news_group_items INSERT (one source-article
-- junction row). Executed by llm_agents/llm_qa.py (ensure_news_group)
-- via asyncpg executemany — one row per source news_id of the group.
--
-- Params (asyncpg $n, per row, in order):
--   $1 news_group_id  BIGINT — the parent group
--   $2 news_id        BIGINT — the source article (must already exist
--                     in text.news — the FK)
--
-- Idempotent on the (news_group_id, news_id) junction.
INSERT INTO text.news_group_items (news_group_id, news_id)
VALUES ($1, $2) ON CONFLICT DO NOTHING
