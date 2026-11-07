-- SQL write action — llm.llm_qa_keywords_by_ask INSERT (one derived
-- keyword row). Executed by llm_agents/llm_ask/storage.py (_persist_ask)
-- via asyncpg executemany inside the per-ask transaction — the
-- deterministic keyword index (instrument codes/names, product/page,
-- in-plot state values + searchKeywords, series names), like
-- text.news_keywords for the news corpus.
--
-- Params (asyncpg $n, per row, in order):
--   $1 ask_id    BIGINT — the parent ask row
--   $2 keyword   TEXT — the derived term (length-capped host-side)
--   $3 kind      TEXT — the keyword's source kind (code | name |
--                product | page | state | search | series)
--   $4 code      TEXT | NULL — the ask's primary instrument code
--   $5 product   TEXT | NULL — the product/page tag
--   $6 ask_day   DATE — the ask's Asia/Shanghai date
INSERT INTO llm.llm_qa_keywords_by_ask
    (ask_id, keyword, kind, code, product, ask_day)
VALUES ($1, $2, $3, $4, $5, $6) ON CONFLICT DO NOTHING
