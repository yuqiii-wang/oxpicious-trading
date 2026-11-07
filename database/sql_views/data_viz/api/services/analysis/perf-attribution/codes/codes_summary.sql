-- Perf-attr codes list ('summary' codeSource) — one row per subject code
-- with first/last date, n_dates and the benchmark list, read from the
-- stats.cross_stats_code_summary rollup (millisecond reads; the live
-- variant in codes_live.sql only covers the fresh-DB fallback). The latest
-- identity name is LEFT JOINed per code.
-- Serves GET /api/analysis/perf-attr/codes.
-- args:
--   sec_type: value text — cross_stats sec_type
--   name_table: fragment — PERF_ATTR_NAME_TABLE[sec_type] identity table
    WITH latest_name AS (
      SELECT DISTINCT ON (code) code, name
      FROM {{name_table}}
      ORDER BY code, date DESC
    )
    SELECT
      s.code,
      COALESCE(n.name, '') AS name,
      s.first_date,
      s.last_date,
      s.n_dates,
      s.benchmarks
    FROM (
    SELECT
      cs.code,
      cs.first_date,
      cs.last_date,
      cs.n_dates,
      cs.benchmarks
    FROM stats.cross_stats_code_summary cs
    WHERE cs.sec_type = {{sec_type}}::text) s
    LEFT JOIN latest_name n ON n.code = s.code
    ORDER BY s.n_dates DESC NULLS LAST, s.code
