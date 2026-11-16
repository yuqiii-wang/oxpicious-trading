-- DB tab — ONE table's write workload trend on the primary (instance 0):
-- write/read rates + delta counts + the write_flag verdict per active
-- pass, over a trailing window. Activity-sparse rows — idle gaps simply
-- have no points. Serves GET /api/monitor/db/trend.
-- FAMILY-AWARE: when the named table is a live partitioned parent, the
-- audit for a pass aggregates the whole family (the parent's own
-- write-through rows are idle zeros — every pass' family-wide write/read
-- load lives on the partitions, and the sampler stamps each pass' rows
-- with one shared checked_at, so GROUP BY checked_at sums exactly one
-- pass): rates and delta counts summed, write_flag the max family
-- severity. A plain table (or a partition) stays a family of one —
-- its per-table rows, unchanged. is_family lets the API caption it.
-- args:
--   schema: value text — the table's schema (compared as a VALUE)
--   table: value text — the table name (compared as a VALUE)
--   hours: value int — trailing window in hours (clamped 0-168);
--      0 = ARCHIVE: no lower bound — the full retained history (the
--      sampler's --retain-days prunes the table, so the predicate's
--      OR-arm simply wins and every retained pass is eligible)
WITH fam AS (
    -- the audited table itself (family of one when not a partitioned parent)
    SELECT {{schema}}::text AS table_schema, {{table}}::text AS table_name
    UNION ALL
    -- its live partition children (0 rows when the table is not a parent)
    SELECT pn.nspname, c.relname
    FROM pg_partitioned_table pt
    JOIN pg_class pc ON pc.oid = pt.partrelid
    JOIN pg_namespace pn ON pn.oid = pc.relnamespace
    JOIN pg_inherits i ON i.inhparent = pc.oid
    JOIN pg_class c ON c.oid = i.inhrelid
    WHERE pn.nspname = {{schema}}::text
      AND pc.relname = {{table}}::text
), pts AS (
    SELECT a.checked_at,
           max(a.window_s) AS window_s,
           sum(a.write_rate) AS write_rate,
           sum(a.read_rate) AS read_rate,
           sum(a.ins_rows) AS ins_rows,
           sum(a.upd_rows) AS upd_rows,
           sum(a.del_rows) AS del_rows,
           max(a.write_flag) AS write_flag
    FROM monitor.db_table_audit a
    JOIN fam f ON f.table_schema = a.table_schema AND f.table_name = a.table_name
    WHERE a.instance = 0
      AND ({{hours}}::int <= 0 OR a.checked_at >= now() - make_interval(hours => {{hours}}::int))
    GROUP BY a.checked_at
)
SELECT pts.checked_at, pts.window_s, pts.write_rate, pts.read_rate,
       pts.ins_rows, pts.upd_rows, pts.del_rows, pts.write_flag,
       EXISTS (
           SELECT 1
           FROM pg_partitioned_table pt
           JOIN pg_class pc ON pc.oid = pt.partrelid
           JOIN pg_namespace pn ON pn.oid = pc.relnamespace
           WHERE pn.nspname = {{schema}}::text
             AND pc.relname = {{table}}::text
       ) AS is_family
FROM pts
ORDER BY pts.checked_at
