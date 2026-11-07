-- margins — MAX(date) across the selected sec_types' source
-- tables (the universe filter's ref_date; tracks the latest
-- available data instead of wall-clock today).
-- args:
--   source_union: fragment — the UNION ALL of per-table
--       MAX(date) legs composed in fetch_latest_source_date
SELECT MAX(d) AS latest FROM ({{source_union}}) sub