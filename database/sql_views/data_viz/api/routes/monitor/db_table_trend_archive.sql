-- DB tab — ONE table's workload trend, ARCHIVE SOURCE: the computed daily
-- rollup monitor.db_table_audit_archive as a per-GMT-day series (the
-- operational-day rollup is the archive's grain — one point per day the
-- table was active in any retained day, so the chart spans the FULL
-- archive history, not a trailing window). Write side = primary
-- (instance 0) like db_table_trend. FAMILY-AWARE like db_table_trend:
-- when the named table is a live partitioned parent, each day aggregates
-- the whole family (members' day peaks SUMMED — the members' peaks may
-- fall at different times of the day, so the family point is an upper
-- approximation of the family's combined rate; delta day-sums sum
-- exactly), write_flag the max family severity; a plain table (or a
-- partition) stays a family of one. checked_at is the day's last active
-- stamp (tooltip material); date is the GMT operational day ITSELF,
-- carried as text because the browser-local calendar date of checked_at
-- can skew a day off the archive's UTC-day grain. window_s is constant
-- NULL (no per-pass window here). Serves
-- GET /api/monitor/db/trend?source=archive.
-- args:
--   schema: value text — the table's schema (compared as a VALUE)
--   table: value text — the table name (compared as a VALUE)
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
    SELECT a.date,
           max(a.last_active_at) AS checked_at,
           sum(a.peak_write_rate) AS write_rate,
           sum(a.peak_read_rate) AS read_rate,
           sum(a.ins_rows) AS ins_rows,
           sum(a.upd_rows) AS upd_rows,
           sum(a.del_rows) AS del_rows,
           max(a.max_write_flag) AS write_flag
    FROM monitor.db_table_audit_archive a
    JOIN fam f ON f.table_schema = a.table_schema AND f.table_name = a.table_name
    WHERE a.instance = 0
    GROUP BY a.date
)
SELECT pts.date::text AS date, pts.checked_at,
       NULL::integer AS window_s,
       pts.write_rate, pts.read_rate,
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
ORDER BY pts.date
