-- Perf-attr codes list ('live' codeSource) — the historical two-CTE
-- full-table aggregate over stats.cross_stats (fallback only, used while
-- the cross_stats_code_summary rollup has no rows for the sec_type yet):
-- per-code MIN/MAX/COUNT dates plus the ARRAY_AGG benchmark list, with the
-- latest identity name LEFT JOINed per code.
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
    WITH code_stats AS (
      SELECT
        code,
        MIN(date) AS first_date,
        MAX(date) AS last_date,
        COUNT(DISTINCT date) AS n_dates
      FROM stats.cross_stats
      WHERE sec_type = {{sec_type}}::text
      GROUP BY code
    ),
    bench_list AS (
      SELECT code, ARRAY_AGG(DISTINCT benchmark_code ORDER BY benchmark_code) AS benchmarks
      FROM stats.cross_stats
      WHERE sec_type = {{sec_type}}::text
      GROUP BY code
    )
    SELECT
      cs.code,
      cs.first_date,
      cs.last_date,
      cs.n_dates,
      COALESCE(bl.benchmarks, '{}') AS benchmarks
    FROM code_stats cs
    LEFT JOIN bench_list bl ON bl.code = cs.code) s
    LEFT JOIN latest_name n ON n.code = s.code
    ORDER BY s.n_dates DESC NULLS LAST, s.code
