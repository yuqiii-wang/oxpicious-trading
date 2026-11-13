-- SQL write action — llm.kg_rule_syncs UPSERT (one rule's corpus
-- fingerprint). Executed by llm_agents/kg/static_rules/writer.py
-- (StaticRulesWriter.apply) as the LAST statement of the sync's ONE
-- transaction: a crash before the commit leaves no stamp, and the
-- read-time freshness gate (static_rules/freshness.py) then re-syncs
-- on the next load — the safe direction. max_mtime is the corpus's
-- MAX file stat time recorded by the same scan that produced the
-- rows, so the gate's comparison is mtime-to-mtime (no clock skew).
--
-- Params (asyncpg $n, in order):
--   $1 rule_name    TEXT — the StaticRule.name ('sql_view_semantics')
--   $2 namespace    SMALLINT — the KG namespace the rule writes
--   $3 corpus_root  TEXT — the scanned dir, repo-relative posix
--   $4 file_count   INTEGER — the corpus's .sql file count
--   $5 max_mtime    TIMESTAMPTZ — the corpus's MAX(st_mtime) (UTC)
INSERT INTO llm.kg_rule_syncs
    (rule_name, namespace, corpus_root, file_count, max_mtime)
VALUES ($1, $2, $3, $4, $5)
ON CONFLICT (rule_name) DO UPDATE
    SET namespace   = EXCLUDED.namespace,
        corpus_root = EXCLUDED.corpus_root,
        file_count  = EXCLUDED.file_count,
        max_mtime   = EXCLUDED.max_mtime,
        synced_at   = now()
