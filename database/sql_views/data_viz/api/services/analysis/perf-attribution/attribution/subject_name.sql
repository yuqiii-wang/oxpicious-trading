-- Perf-attr attribution — latest identity name for the subject code's
-- variants (first row wins; the service reads nameRows[0]).
-- Serves GET /api/analysis/perf-attr/attribution.
-- args:
--   codes: value text[] — the subject's code variants (codeVariants)
--   name_table: fragment — PERF_ATTR_NAME_TABLE[sec_type] identity table
SELECT DISTINCT ON (code) code, name FROM {{name_table}} WHERE code = ANY({{codes}}::text[]) ORDER BY code, date DESC
