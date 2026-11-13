-- SQL write action — llm.kg_edges DELETE (every edge touching a stale
-- OWNED table entity). Executed by
-- llm_agents/kg/static_rules/writer.py (StaticRulesWriter.apply_tables)
-- FIRST of the prune pair, inside the sync's one transaction: an
-- authored table payload (a database/sql DDL file's leading banner)
-- that was removed leaves its entity ledger-listed but absent — its
-- edges die before the entity does, so no ghost endpoints survive the
-- diff. The companion delete_stale_rule_tables.sql removes the entities
-- themselves. --force passes an empty keep-list: the full
-- purge-then-rebuild convention (the rule's rows are fully derived from
-- the corpus).
--
-- Params (asyncpg $n, in order):
--   $1 rule_name  TEXT — the StaticRule.name ('db_semantics_tables')
--   $2 names      TEXT[] — the corpus table names to KEEP
DELETE FROM llm.kg_edges x
WHERE x.namespace = 0
  AND x.entity_ids && ARRAY(
      SELECT e.entity_id
      FROM llm.kg_entities e
      WHERE e.namespace = 0
        AND e.entity_type = 'table'
        AND e.name IN (SELECT r.entity_name FROM llm.kg_rule_entities r
                       WHERE r.rule_name = $1
                         AND r.entity_name <> ALL($2::text[])))
