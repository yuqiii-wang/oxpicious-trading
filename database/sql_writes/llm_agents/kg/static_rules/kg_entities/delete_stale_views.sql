-- SQL write action — llm.kg_entities DELETE (the stale ns0 sql_views
-- template entities). Executed by llm_agents/kg/static_rules/writer.py
-- (StaticRulesWriter.apply) at the head of the sync: the rule owns
-- every ns0 'view' entity whose name is a slash path (a scanned
-- database/sql_views template name) — a template deleted or renamed
-- in the corpus prunes its entity here, before the edge
-- re-derivation, so no ghost endpoint survives. Schema-qualified names
-- (the hand-seeded stats.v_* DB views) never contain a slash and are
-- structurally untouchable by this action. --force passes an empty
-- keep-list: the full purge-then-rebuild convention (the rule's rows
-- are fully derived, so the sync is the same rebuild with pruning).
--
-- Params (asyncpg $n, in order):
--   $1 names  TEXT[] — the scanned template names to KEEP
DELETE FROM llm.kg_entities
WHERE namespace = 0
  AND entity_type = 'view'
  AND name LIKE '%/%'
  AND NOT (name = ANY($1::text[]))
