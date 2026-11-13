-- KG static rules — the database's own relation inventory behind the
-- sql_views corpus rule (static_rules/sql_view_semantics.py): one row
-- per table / view / materialized view the database actually serves,
-- so the rule's parsed schema-qualified references only become
-- reads_from edges when BOTH endpoints resolve to a live relation
-- (alias refs like t.date / b.code never match a schema and drop out
-- here, as do pg_catalog / information_schema helpers).
-- obj_description carries each relation's COMMENT ON TABLE prose — the
-- grounded mechanical description for referenced tables that have no
-- ns0 entity yet (created with empty tags; a hand-seeded row is never
-- overridden by the rule).
-- serves: llm_agents.kg.static_rules.sql_view_semantics.ViewSemanticsRule
-- args: none
SELECT n.nspname || '.' || c.relname AS relation_name,
       c.relkind,
       obj_description(c.oid, 'pg_class') AS relation_comment
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind IN ('r', 'p', 'v', 'm')
  AND n.nspname NOT LIKE 'pg\_%'
  AND n.nspname <> 'information_schema'
ORDER BY 1
