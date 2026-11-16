-- DB tab — the per-table workload panel: MAIN TABLES ONLY. Partition
-- children (pg_inherits under a live partitioned parent — 100+ families
-- of 8-32 partitions each, which alone flood the list) are FOLDED INTO
-- ONE row per family root: the parent's own audit row (usually a zero
-- write-through census row) + every live child's latest row aggregated —
-- write/read rates and ins/upd/del delta counts summed, live/dead tup and
-- size summed, write_flag the max family severity, stamps the latest of
-- the family. The partitions themselves load lazily via
-- db_table_partitions (GET /db/partitions) when a row is expanded.
-- Rows are ACTIVITY-SPARSE (idle tables write nothing), so "latest row
-- within N hours" = "last active pass"; audited names that no longer
-- exist in the catalog (dropped tables' trailing rows) are dropped from
-- the panel. Read rate is the EFFECTIVE rate the API surfaces (replica
-- side, falling back to the primary's own) — summed over the family, so
-- a family of one (a plain table) keeps its exact per-table value.
-- Serves GET /api/monitor/db/tables.
-- args:
--   hours: value int — trailing window in hours (clamped 0-168);
--      0 = ARCHIVE: no lower bound — the full retained history (the
--      sampler's --retain-days prunes the table, so the predicate's
--      OR-arm simply wins and every retained row is eligible)
--   order_expr: fragment — a ROUTE-WHITELISTED ORDER BY expression over
--      the outer SELECT's columns (workload: write + read rate DESC /
--      recent: the later of the two family stamps DESC); never user text
--      verbatim
--   limit: value int — max rows after ordering (clamped 1-2000)
WITH p AS (
    SELECT DISTINCT ON (table_schema, table_name)
           table_schema, table_name, checked_at, window_s,
           write_rate, ins_rows, upd_rows, del_rows, read_rate,
           n_live_tup, n_dead_tup, table_size_bytes, write_flag
    FROM monitor.db_table_audit
    WHERE instance = 0
      AND ({{hours}}::int <= 0 OR checked_at >= now() - make_interval(hours => {{hours}}::int))
    ORDER BY table_schema, table_name, checked_at DESC
), r AS (
    SELECT DISTINCT ON (table_schema, table_name)
           table_schema, table_name, checked_at, read_rate
    FROM monitor.db_table_audit
    WHERE instance = 1
      AND ({{hours}}::int <= 0 OR checked_at >= now() - make_interval(hours => {{hours}}::int))
    ORDER BY table_schema, table_name, checked_at DESC
), rel AS (
    -- the live catalog: audited names not in here are dropped tables —
    -- their trailing rows stay in the audit but are not panel material
    SELECT n.nspname AS schema, c.relname AS table_name
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE c.relkind IN ('r', 'p', 'm')
), child AS (
    -- every live partition child mapped to its (live, partitioned) parent;
    -- this cluster partitions one level deep, so one hop reaches the root
    SELECT pn.nspname AS parent_schema, pc.relname AS parent_name,
           cn.nspname AS schema, c.relname AS table_name
    FROM pg_inherits i
    JOIN pg_class c  ON c.oid = i.inhrelid
    JOIN pg_namespace cn ON cn.oid = c.relnamespace
    JOIN pg_class pc ON pc.oid = i.inhparent
    JOIN pg_namespace pn ON pn.oid = pc.relnamespace
    JOIN pg_partitioned_table pt ON pt.partrelid = pc.oid
), base AS (
    -- one row per audited table: both sides joined, read rate effective
    SELECT COALESCE(p.table_schema, r.table_schema) AS table_schema,
           COALESCE(p.table_name, r.table_name) AS table_name,
           p.checked_at AS write_checked_at, p.window_s,
           p.write_rate, p.ins_rows, p.upd_rows, p.del_rows,
           r.checked_at AS read_checked_at,
           COALESCE(r.read_rate, p.read_rate) AS read_rate,
           p.n_live_tup, p.n_dead_tup, p.table_size_bytes, p.write_flag
    FROM p
    FULL OUTER JOIN r USING (table_schema, table_name)
), fam AS (
    -- each audited table rolls up to its family root (itself when not a
    -- partition child); sums skip NULLs, so a family of one with NULL
    -- rates stays NULL — the plain-table semantics are unchanged
    SELECT COALESCE(ch.parent_schema, b.table_schema) AS table_schema,
           COALESCE(ch.parent_name, b.table_name) AS table_name,
           max(b.write_checked_at) AS write_checked_at,
           max(b.window_s) AS window_s,
           sum(b.write_rate) AS write_rate,
           sum(b.ins_rows) AS ins_rows,
           sum(b.upd_rows) AS upd_rows,
           sum(b.del_rows) AS del_rows,
           max(b.read_checked_at) AS read_checked_at,
           sum(b.read_rate) AS read_rate,
           sum(b.n_live_tup) AS n_live_tup,
           sum(b.n_dead_tup) AS n_dead_tup,
           sum(b.table_size_bytes) AS table_size_bytes,
           max(b.write_flag) AS write_flag
    FROM base b
    JOIN rel ON rel.schema = b.table_schema AND rel.table_name = b.table_name
    LEFT JOIN child ch ON ch.schema = b.table_schema AND ch.table_name = b.table_name
    GROUP BY 1, 2
)
SELECT f.table_schema, f.table_name,
       f.write_checked_at, f.window_s,
       f.write_rate, f.ins_rows, f.upd_rows, f.del_rows,
       f.read_checked_at, f.read_rate,
       f.n_live_tup, f.n_dead_tup, f.table_size_bytes, f.write_flag,
       pc.n_parts AS partition_count
FROM fam f
LEFT JOIN (
    SELECT parent_schema, parent_name, count(*) AS n_parts
    FROM child
    GROUP BY parent_schema, parent_name
) pc ON pc.parent_schema = f.table_schema AND pc.parent_name = f.table_name
ORDER BY {{order_expr}}
LIMIT {{limit}}
