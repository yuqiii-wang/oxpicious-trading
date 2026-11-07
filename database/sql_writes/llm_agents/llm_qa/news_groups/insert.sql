-- SQL write action — text.news_groups INSERT (one Q&A source group).
-- Executed by llm_agents/llm_qa.py (ensure_news_group) when an answer
-- carries news provenance: the source-article news_ids group into one
-- row, linked through text.news_group_items and referenced by
-- llm.llm_qa.news_group_id.
--
-- Params (asyncpg $n, in order):
--   $1 news_group_name  TEXT — the group's name ('qa: <question[:60]>'
--                       or the qa-<timestamp> default)
--
-- Returns the new news_group_id. The junction insert is idempotent
-- (insert_rows) so re-storing an answer with the same sources reuses
-- the group without duplication.
INSERT INTO text.news_groups (news_group_name) VALUES ($1)
RETURNING news_group_id
