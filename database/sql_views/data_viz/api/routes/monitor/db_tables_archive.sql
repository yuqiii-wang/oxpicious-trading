-- DB tab — the per-table workload panel, ARCHIVE SOURCE: the computed
-- daily rollup monitor.db_table_audit_archive (one record per GMT day /
-- table / instance, kept indefinitely — the sampler's ~30d per-pass
-- retention does not bound this view) collapsed to a LIFETIME aggregate
-- per table: day sums of ins/upd/del added up, peak write/read rates the
-- worst day's, write_flag the worst-ever verdict, live/dead tup and size
-- the LATEST date's end-of-day snapshots, stamps the most recent active
-- day. Same side semantics as db_tables_latest: the write side from the
-- primary (instance 0), the read rate EFFECTIVE (replica peak, falling
-- back to the primary's own). Output columns match db_tables_latest 1:1
-- (window_s constant NULL — no per-pass window exists here) so the route
-- reuses its order whitelist and row mapping verbatim. Archive rows of
-- DROPPED tables are kept (no live-catalog filter — the archive is their
-- only record); LIVE partition families still fold into one aggregated
-- parent row (the child CTE is live-catalog based, so a dropped family
-- stays unfolded). Serves GET /api/monitor/db/tables?source=archive.
-- args:
--   order_expr: fragment — a ROUTE-WHITELISTED ORDER BY expression over
--      the outer SELECT's columns (same whitelist as the live panel:
--      workload: write + read rate DESC / recent: the later of the two
--      family stamps DESC); never user text verbatim
--   limit: value int — max rows after ordering (clamped 1-2000)
WITH child AS (
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
), p AS (
    -- per-table lifetime WRITE-side aggregate over the primary's rows
    SELECT table_schema, table_name,
           max(last_active_at) AS write_checked_at,
           max(peak_write_rate) AS write_rate,
           max(peak_read_rate) AS read_rate_primary,
           sum(ins_rows) AS ins_rows,
           sum(upd_rows) AS upd_rows,
           sum(del_rows) AS del_rows,
           max(max_write_flag) AS write_flag
    FROM monitor.db_table_audit_archive
    WHERE instance = 0
    GROUP BY table_schema, table_name
), r AS (
    -- per-table lifetime READ-side aggregate over the replica's rows
    SELECT table_schema, table_name,
           max(last_active_at) AS read_checked_at,
           max(peak_read_rate) AS read_rate_replica
    FROM monitor.db_table_audit_archive
    WHERE instance = 1
    GROUP BY table_schema, table_name
), snap AS (
    -- the LATEST archived day's end-of-day snapshots per table (the
    -- growth-trending columns; NULL when the table never archived a
    -- primary row)
    SELECT DISTINCT ON (table_schema, table_name)
           table_schema, table_name,
           end_n_live_tup AS n_live_tup,
           end_n_dead_tup AS n_dead_tup,
           end_table_size_bytes AS table_size_bytes
    FROM monitor.db_table_audit_archive
    WHERE instance = 0
    ORDER BY table_schema, table_name, date DESC
), base AS (
    -- one row per archived table: both sides joined, read rate effective
    SELECT COALESCE(p.table_schema, r.table_schema) AS table_schema,
           COALESCE(p.table_name, r.table_name) AS table_name,
           p.write_checked_at,
           NULL::integer AS window_s,
           p.write_rate, p.ins_rows, p.upd_rows, p.del_rows,
           COALESCE(r.read_checked_at, p.write_checked_at) AS read_checked_at,
           COALESCE(r.read_rate_replica, p.read_rate_primary) AS read_rate,
           snap.n_live_tup, snap.n_dead_tup, snap.table_size_bytes,
           p.write_flag
    FROM p
    FULL OUTER JOIN r USING (table_schema, table_name)
    LEFT JOIN snap USING (table_schema, table_name)
), fam AS (
    -- each archived table rolls up to its LIVE family root (itself when
    -- not a partition child); sums skip NULLs, so a family of one keeps
    -- its exact per-table values
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
