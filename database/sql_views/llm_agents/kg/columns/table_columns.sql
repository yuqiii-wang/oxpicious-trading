-- KG tool column auto-load — the pg_catalog introspection behind the
-- knowledge-graph service's table-entity column blocks (columns.py):
-- one row per column of each requested schema-qualified table, with its
-- type and its COMMENT ON COLUMN prose, so kg_search / kg_expand results
-- carry the exact schema without a db_describe_table round trip.
-- serves: llm_agents.kg.columns.TableColumnCatalog (kg_search / kg_expand)
-- args:
--   tables: value text array — schema-qualified names ('schema.table')
SELECT n.nspname || '.' || c.relname AS table_name,
       a.attnum,
       a.attname AS column_name,
       format_type(a.atttypid, a.atttypmod) AS data_type,
       col_description(c.oid, a.attnum) AS column_comment
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_attribute a ON a.attrelid = c.oid
                   AND a.attnum > 0 AND NOT a.attisdropped
WHERE c.relkind IN ('r', 'p', 'v', 'm')
  AND n.nspname || '.' || c.relname = ANY({{tables}}::text[])
ORDER BY n.nspname, c.relname, a.attnum
