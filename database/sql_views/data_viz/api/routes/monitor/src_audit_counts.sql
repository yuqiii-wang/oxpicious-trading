-- Source Data — per-sec_type row counts of the source-data coverage audit
-- (unfiltered header counts; labels decode at the route boundary).
-- Serves GET /api/monitor/src-data-audit.
-- args: none
SELECT sec_type, count(*) AS n FROM monitor.src_data_audit GROUP BY sec_type