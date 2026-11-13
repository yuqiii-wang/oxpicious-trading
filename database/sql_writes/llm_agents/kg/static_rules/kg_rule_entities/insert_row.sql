-- SQL write action — llm.kg_rule_entities INSERT (one ownership-ledger
-- row). Executed by llm_agents/kg/static_rules/writer.py
-- (StaticRulesWriter.apply_tables) via asyncpg executemany after
-- delete_for_rule.sql, inside the same sync transaction: one row per
-- scanned corpus file, so the ledger names exactly the entities the
-- rule owns as of this sync.
--
-- Params (asyncpg $n, per row, in order):
--   $1 rule_name   TEXT — the StaticRule.name ('db_semantics_tables')
--   $2 entity_name TEXT — the schema-qualified table name the corpus
--                  file seeds ('stats.index_basic_stats')
INSERT INTO llm.kg_rule_entities (rule_name, entity_name)
VALUES ($1, $2)
