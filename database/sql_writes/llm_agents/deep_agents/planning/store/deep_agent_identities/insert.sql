-- SQL write action — llm.deep_agent_identities INSERT (the code <->
-- thread_id reverse index). Executed by
-- llm_agents/deep_agents/planning/store.py
-- (PlanStore.record_thread_identity) at RUN START from the request's
-- classification-nav scope picks: one row per subject, so the summary
-- history's per-code read filters on it. Market-wide runs (no pick)
-- create no row.
--
-- Params (asyncpg $n, in order):
--   $1 thread_id  TEXT — the plan thread's id (the chain root)
--   $2 sec_type   SMALLINT — the static.label_dim 'sec_type' code
--                 (etf=0 index=1 stock=2 industry=3; resolved host-side
--                 via _common.labels SEC_TYPE_CODE)
--   $3 code       TEXT — the security code or industry id
--   $4 name       TEXT | NULL — the subject's display name
INSERT INTO llm.deep_agent_identities
    (thread_id, sec_type, code, name, date)
VALUES ($1, $2, $3, $4, (now() AT TIME ZONE 'GMT')::date)
