-- SQL write action — llm.llm_qa UPDATE (re-store one Q&A row in place).
-- Executed by llm_agents/llm_qa.py (upsert_qa) for the news-less case:
-- Postgres UNIQUE treats NULLs as distinct, so (question, NULL group)
-- is matched explicitly on question + news_group_id IS NULL and the row
-- updates instead of inserting a duplicate.
--
-- Params (asyncpg $n, in order):
--   $1  qa_id                  BIGINT — the matched row
--   $2  answer                 TEXT — the re-rendered answer (capped)
--   $3  context                TEXT | NULL — the context passage (capped)
--   $4  category               TEXT | NULL
--   $5  industry_id            TEXT | NULL
--   $6  sector_id              TEXT | NULL
--   $7  llm_model              TEXT | NULL
--   $8  language               TEXT — 'zh' default
--   $9  is_active              BOOLEAN — the soft-delete flag
--   $10 qa_date                TIMESTAMPTZ | NULL — the question's OWN
--                              data date; NULL keeps the stored one
--                              (COALESCE)
--   $11 is_failed_explanation  BOOLEAN — the refusal-boilerplate
--                              derivation over the answer text
--   $12 request_ids            INTEGER[] — the llm.llm_requests backlink
--
-- The row's search_tsv + the lexeme stats refresh follow (the
-- _refresh_qa_search_tsv tail: question A, answer B, context D).
UPDATE llm.llm_qa SET answer = $2, context = $3,
category = $4, industry_id = $5, sector_id = $6,
llm_model = $7, language = $8, is_active = $9,
qa_date = COALESCE($10::timestamptz, qa_date),
is_failed_explanation = $11, request_ids = $12,
updated_at = now()
WHERE qa_id = $1
