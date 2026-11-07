-- Perf-attr code-source probe — does the cross_stats_code_summary rollup
-- have any rows for this sec_type? resolvePerfAttrCodeSource() prefers the
-- rollup ('summary') and falls back to the cross_stats probe
-- (has_cross_stats_rows.sql). No endpoint of its own — input to the
-- perf-attr codes/themes queries.
-- args:
--   sec_type: value text — the perf-attr sec_type probed for
SELECT 1 AS ok FROM stats.cross_stats_code_summary WHERE sec_type = {{sec_type}}::text LIMIT 1
