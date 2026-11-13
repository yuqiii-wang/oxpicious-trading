-- KG tool column auto-load — the PK/index introspection beside
-- table_columns.sql: the primary-key grain and the secondary indexes of
-- each requested schema-qualified table, so kg_search / kg_expand
-- results tell the model WHICH columns key the table (filter on those)
-- and what is indexed (fast predicates) without a db_describe_table
-- round trip. One row per key item: the PK first (ord 0, kind 'pk'),
-- then every non-primary index (ord 1, kind 'idx'; the rendered text
-- keeps the UNIQUE flag and strips the CREATE INDEX prefix down to the
-- column list). Views carry no rows — the render simply omits the
-- key section. Partitioned parents (relkind 'p') surface their own PK
-- and indexes; the partition children are separate relations this
-- query never matches.
-- serves: llm_agents.kg.columns.TableColumnCatalog (kg_search / kg_expand)
-- args:
--   tables: value text array — schema-qualified names ('schema.table')
SELECT n.nspname || '.' || c.relname AS table_name,
       0 AS ord,
       'pk' AS kind,
       pg_get_constraintdef(p.oid, true) AS key_def
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_constraint p ON p.conrelid = c.oid AND p.contype = 'p'
WHERE c.relkind IN ('r', 'p')
  AND n.nspname || '.' || c.relname = ANY({{tables}}::text[])
UNION ALL
SELECT n.nspname || '.' || c.relname,
       1,
       'idx',
       CASE WHEN i.indisunique THEN 'UNIQUE ' ELSE '' END
       || regexp_replace(pg_get_indexdef(i.indexrelid, 0, true),
                         '^CREATE (UNIQUE )?INDEX [^ ]+ ON (ONLY )?[^ ]+'
                         '( USING [a-z]+)? ', '')
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_index i ON i.indrelid = c.oid AND NOT i.indisprimary
WHERE c.relkind IN ('r', 'p')
  AND n.nspname || '.' || c.relname = ANY({{tables}}::text[])
ORDER BY 1, 2, 4
