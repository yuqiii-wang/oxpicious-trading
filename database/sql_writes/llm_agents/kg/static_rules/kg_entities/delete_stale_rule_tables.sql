-- SQL write action — llm.kg_entities DELETE (the stale OWNED table
-- entities). Executed by
-- llm_agents/kg/static_rules/writer.py (StaticRulesWriter.apply_tables)
-- SECOND of the prune pair (its edges died in
-- kg_edges/delete_stale_rule_edges.sql first), inside the sync's one
-- transaction: an authored table payload (a database/sql DDL file's
-- leading banner) that was removed or renamed leaves its entity
-- ledger-listed but absent — the delete drops those entities (no ghost
-- endpoints survive the diff). --force passes an empty keep-list: the
-- full purge-then-rebuild convention (the rule's rows are fully
-- derived from the corpus).
--
-- Params (asyncpg $n, in order):
--   $1 rule_name  TEXT — the StaticRule.name ('db_semantics_tables')
--   $2 names      TEXT[] — the corpus table names to KEEP
DELETE FROM llm.kg_entities e
WHERE e.namespace = 0
  AND e.entity_type = 'table'
  AND e.name IN (SELECT r.entity_name FROM llm.kg_rule_entities r
                 WHERE r.rule_name = $1
                   AND r.entity_name <> ALL($2::text[]))
