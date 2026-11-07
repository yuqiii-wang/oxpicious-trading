-- SQL write action — llm.llm_qa_by_ask INSERT (one AI Ask's history row).
-- Executed by llm_agents/llm_ask/storage.py (_persist_ask) inside the
-- per-ask transaction; convention of this tree:
--   database/sql_writes/<app>/<layer>/<table>/<action>.sql
--
-- Params (asyncpg $n, in order):
--   $1 question       TEXT — the ask's question text
--   $2 answer         TEXT | NULL — the rendered answer (capped
--                     host-side); NULL = the ask failed
--   $3 status         TEXT — 'answered' | 'failed'
--   $4 error_tail     TEXT | NULL — the failure's stderr tail (capped)
--   $5 online_search  BOOLEAN — the payload's web-search toggle
--   $6 search_query   TEXT | NULL — the effective search query
--   $7 provider       TEXT | NULL — the answering provider label
--   $8 llm_model      TEXT | NULL — the answering model
--   $9 code           TEXT | NULL — the payload's primary instrument
--                     code (the hot filter)
--   $10 product       TEXT | NULL — the product/page tag
--   $11 request_ids   INTEGER[] — the ask's llm.llm_requests backlink
--                     (collected fail-soft from flow_start)
--
-- Returns the new ask_id. The row's search_tsv + the lexeme stats
-- refresh follow inside the same transaction (SearchIndexer, TSV_SPECS:
-- question A, answer B; failed asks index the question only).
INSERT INTO llm.llm_qa_by_ask
    (question, answer, status, error_tail, online_search, search_query,
     provider, llm_model, code, product, request_ids)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
RETURNING ask_id
