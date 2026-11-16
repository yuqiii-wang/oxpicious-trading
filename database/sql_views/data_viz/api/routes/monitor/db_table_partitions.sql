-- DB tab — the partitions of ONE partitioned parent, expanded lazily from
-- the workload panel (db_tables_latest folds a whole family into one
-- aggregated parent row; this lists its live children when a row is
-- expanded). Every live partition shows — structure first — carrying the
-- LATEST audit row in the window when the partition was active
-- (activity-sparse: NULL metrics = idle in the window). Same side
-- semantics as db_tables_latest: write side = primary (instance 0),
-- read rate EFFECTIVE (replica side, falling back to the primary's own).
-- Serves GET /api/monitor/db/partitions.
-- args:
--   schema: value text — the parent table's schema (regex-checked by the
--      route before rendering)
--   table: value text — the parent table's name (regex-checked by the
--      route before rendering)
--   hours: value int — trailing window in hours (clamped 0-168);
--      0 = ARCHIVE: no lower bound — the full retained history (the
--      sampler's --retain-days prunes the table, so the predicate's
--      OR-arm simply wins and every retained row is eligible)
WITH child AS (
    SELECT c.relname AS table_name
    FROM pg_class pc
    JOIN pg_namespace pn ON pn.oid = pc.relnamespace
    JOIN pg_partitioned_table pt ON pt.partrelid = pc.oid
    JOIN pg_inherits i ON i.inhparent = pc.oid
    JOIN pg_class c ON c.oid = i.inhrelid
    JOIN pg_namespace cn ON cn.oid = c.relnamespace
    WHERE pn.nspname = {{schema}}::text
      AND pc.relname = {{table}}::text
), p AS (
    SELECT DISTINCT ON (a.table_name)
           a.table_name, a.checked_at, a.window_s,
           a.write_rate, a.ins_rows, a.upd_rows, a.del_rows, a.read_rate,
           a.n_live_tup, a.n_dead_tup, a.table_size_bytes, a.write_flag
    FROM monitor.db_table_audit a
    JOIN child ch ON ch.table_name = a.table_name
    WHERE a.instance = 0
      AND a.table_schema = {{schema}}::text
      AND ({{hours}}::int <= 0 OR a.checked_at >= now() - make_interval(hours => {{hours}}::int))
    ORDER BY a.table_name, a.checked_at DESC
), r AS (
    SELECT DISTINCT ON (a.table_name)
           a.table_name, a.checked_at, a.read_rate
    FROM monitor.db_table_audit a
    JOIN child ch ON ch.table_name = a.table_name
    WHERE a.instance = 1
      AND a.table_schema = {{schema}}::text
      AND ({{hours}}::int <= 0 OR a.checked_at >= now() - make_interval(hours => {{hours}}::int))
    ORDER BY a.table_name, a.checked_at DESC
)
SELECT {{schema}}::text AS table_schema, ch.table_name,
       p.checked_at AS write_checked_at, p.window_s,
       p.write_rate, p.ins_rows, p.upd_rows, p.del_rows,
       r.checked_at AS read_checked_at,
       COALESCE(r.read_rate, p.read_rate) AS read_rate,
       p.n_live_tup, p.n_dead_tup, p.table_size_bytes, p.write_flag
FROM child ch
LEFT JOIN p ON p.table_name = ch.table_name
LEFT JOIN r ON r.table_name = ch.table_name
ORDER BY ch.table_name
