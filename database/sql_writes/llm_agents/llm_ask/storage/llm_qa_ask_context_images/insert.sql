-- SQL write action — llm.llm_qa_ask_context_images INSERT (the ask <->
-- screenshot junction row). Executed by llm_agents/llm_ask/storage.py
-- (_persist_ask) per stored screenshot inside the per-ask transaction.
--
-- Params (asyncpg $n, in order):
--   $1 ask_id    BIGINT — the parent ask row
--   $2 image_id  BIGINT — the src_images row (deduped by content_hash)
--   $3 position  INTEGER — the screenshot's order in the payload
INSERT INTO llm.llm_qa_ask_context_images
    (ask_id, image_id, position)
VALUES ($1, $2, $3) ON CONFLICT DO NOTHING
