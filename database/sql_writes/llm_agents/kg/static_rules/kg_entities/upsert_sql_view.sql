-- SQL write action — llm.kg_entities UPSERT (one ns0 sql_views template
-- entity). Executed by llm_agents/kg/static_rules/writer.py
-- (StaticRulesWriter.apply) via asyncpg executemany inside the rule's
-- one transaction: the entity row for ONE scanned database/sql_views
-- template, entity_type 'view', name = the template's slash path (the
-- same render name the sql_views loaders use). Idempotent on
-- uq_kg_entities_ns_type_name — description/tags refresh in place,
-- entity_id / created_at never touched (stable ids are what the
-- reads_from edges reference).
--
-- Params (asyncpg $n, per row, in order):
--   $1 name        TEXT — the template's slash path
--                  ('analysis/margins/fetch/index_margin_series')
--   $2 description TEXT — the template's own leading-comment clause
--                  (the harvested business meaning; never invented)
--   $3 tags        TEXT[] — the referenced relations' bare names +
--                  the template's path segments
INSERT INTO llm.kg_entities (namespace, entity_type, name, description, tags)
VALUES (0, 'view', $1, $2, $3)
ON CONFLICT (namespace, entity_type, name) DO UPDATE
    SET description = EXCLUDED.description,
        tags        = EXCLUDED.tags
