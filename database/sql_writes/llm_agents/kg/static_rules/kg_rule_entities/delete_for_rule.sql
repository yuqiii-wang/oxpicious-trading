-- SQL write action — llm.kg_rule_entities DELETE (one rule's whole
-- ownership ledger). Executed by llm_agents/kg/static_rules/writer.py
-- (StaticRulesWriter.apply_tables) inside the sync transaction right
-- before the re-insert: the ledger mirrors the corpus exactly (delete +
-- executemany INSERT of the scanned corpus names), so a corpus file
-- deleted since the last sync drops out of the ledger and the
-- delete_stale_rule_tables prune of the NEXT sync no longer sees it —
-- ownership always reflects the corpus as of the last successful sync.
--
-- Params (asyncpg $n, in order):
--   $1 rule_name  TEXT — the StaticRule.name ('db_semantics_tables')
DELETE FROM llm.kg_rule_entities
WHERE rule_name = $1
