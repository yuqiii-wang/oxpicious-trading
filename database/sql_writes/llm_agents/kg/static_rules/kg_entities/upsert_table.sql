-- SQL write action — llm.kg_entities UPSERT (one ns0 authored TABLE
-- entity from a database/sql DDL file's leading banner). Executed by
-- llm_agents/kg/static_rules/writer.py (StaticRulesWriter.apply_tables)
-- via asyncpg executemany inside the rule's one transaction. The rule
-- OWNS these rows (the hand seeds no longer carry them — they were
-- migrated to the corpus), so the upsert refreshes description AND
-- tags unconditionally on uq_kg_entities_ns_type_name; entity_id /
-- created_at never touched (stable ids are what edges reference).
--
-- Params (asyncpg $n, per row, in order):
--   $1 name        TEXT — the schema-qualified table name
--                  ('stats.index_basic_stats' — the corpus file's stem)
--   $2 description TEXT — the corpus file's `-- description:` line
--   $3 tags        TEXT[] — the corpus file's `-- tags:` list
--                  (bilingual trader vocabulary, comma-split)
INSERT INTO llm.kg_entities (namespace, entity_type, name, description, tags)
VALUES (0, 'table', $1, $2, $3)
ON CONFLICT (namespace, entity_type, name) DO UPDATE
    SET description = EXCLUDED.description,
        tags        = EXCLUDED.tags
