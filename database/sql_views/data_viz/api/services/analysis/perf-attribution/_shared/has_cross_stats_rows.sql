-- Perf-attr code-source probe — does stats.cross_stats have any rows for
-- this sec_type? resolvePerfAttrCodeSource() returns 'live' when it does
-- and 'empty' otherwise (callers skip the table scan entirely). No endpoint
-- of its own — input to the perf-attr codes/themes queries.
-- args:
--   sec_type: value text — the perf-attr sec_type probed for
SELECT 1 AS ok FROM stats.cross_stats WHERE sec_type = {{sec_type}}::text LIMIT 1
