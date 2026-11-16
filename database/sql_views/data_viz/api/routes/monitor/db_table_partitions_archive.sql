-- DB tab — the partitions of ONE partitioned parent, expanded lazily from
-- the workload panel, ARCHIVE SOURCE: the computed daily rollup
-- monitor.db_table_audit_archive collapsed to a LIFETIME aggregate per
-- partition (day sums of ins/upd/del added up, peak rates the worst
-- day's, write_flag the worst-ever verdict, live/dead tup and size the
-- LATEST date's end-of-day snapshots, stamps the most recent active day).
-- Output columns match db_table_partitions 1:1 (window_s constant NULL —
-- no per-pass window exists here) so the route reuses its row mapping
-- verbatim. Same side semantics as the live view: write side = primary
-- (instance 0), read rate EFFECTIVE (replica peak, falling back to the
-- primary's own). Every live partition shows — structure first — carrying
-- NULLs when the archive has no rows for it yet. Serves
-- GET /api/monitor/db/partitions?source=archive.
-- args:
--   schema: value text — the parent table's schema (regex-checked by the
--      route before rendering)
--   table: value text — the parent table's name (regex-checked by the
--      route before rendering)
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
    -- per-partition lifetime WRITE-side aggregate over the primary's rows
    SELECT a.table_name,
           max(a.last_active_at) AS write_checked_at,
           max(a.peak_write_rate) AS write_rate,
           max(a.peak_read_rate) AS read_rate_primary,
           sum(a.ins_rows) AS ins_rows,
           sum(a.upd_rows) AS upd_rows,
           sum(a.del_rows) AS del_rows,
           max(a.max_write_flag) AS write_flag
    FROM monitor.db_table_audit_archive a
    JOIN child ch ON ch.table_name = a.table_name
    WHERE a.instance = 0
      AND a.table_schema = {{schema}}::text
    GROUP BY a.table_name
), r AS (
    -- per-partition lifetime READ-side aggregate over the replica's rows
    SELECT a.table_name,
           max(a.last_active_at) AS read_checked_at,
           max(a.peak_read_rate) AS read_rate_replica
    FROM monitor.db_table_audit_archive a
    JOIN child ch ON ch.table_name = a.table_name
    WHERE a.instance = 1
      AND a.table_schema = {{schema}}::text
    GROUP BY a.table_name
), snap AS (
    -- the LATEST archived day's end-of-day snapshots per partition
    SELECT DISTINCT ON (a.table_name)
           a.table_name,
           a.end_n_live_tup AS n_live_tup,
           a.end_n_dead_tup AS n_dead_tup,
           a.end_table_size_bytes AS table_size_bytes
    FROM monitor.db_table_audit_archive a
    JOIN child ch ON ch.table_name = a.table_name
    WHERE a.instance = 0
      AND a.table_schema = {{schema}}::text
    ORDER BY a.table_name, a.date DESC
)
SELECT {{schema}}::text AS table_schema, ch.table_name,
       p.write_checked_at, NULL::integer AS window_s,
       p.write_rate, p.ins_rows, p.upd_rows, p.del_rows,
       COALESCE(r.read_checked_at, p.write_checked_at) AS read_checked_at,
       COALESCE(r.read_rate_replica, p.read_rate_primary) AS read_rate,
       snap.n_live_tup, snap.n_dead_tup, snap.table_size_bytes, p.write_flag
FROM child ch
LEFT JOIN p ON p.table_name = ch.table_name
LEFT JOIN r ON r.table_name = ch.table_name
LEFT JOIN snap ON snap.table_name = ch.table_name
ORDER BY ch.table_name
