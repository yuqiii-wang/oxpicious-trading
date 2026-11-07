-- Industry Corr Offsets coverage — the distinct benchmark_code values
-- materialized in analysis_composites.industry_corr_benchmark_offsets,
-- with date bounds, distinct-industry counts, and display label (drives
-- the benchmark dropdown; defaults to 000300 when empty). Inner DISTINCT
-- branches dedupe each pair endpoint BEFORE the UNION so the count stays
-- a hash pass instead of an external-merge sort.
-- Serves GET /api/analysis/industry-corr-offsets/benchmarks.
-- args: none
    WITH bounds AS (
      SELECT benchmark_code, MIN(start_date) AS first_start_date,
                               MAX(start_date) AS last_start_date
      FROM analysis_composites.industry_corr_benchmark_offsets
      GROUP BY benchmark_code
    ),
    counts AS (
      SELECT benchmark_code, COUNT(*) AS n_industries
      FROM (
        SELECT DISTINCT benchmark_code, industry_id AS ind
        FROM analysis_composites.industry_corr_benchmark_offsets
        UNION
        SELECT DISTINCT benchmark_code, benchmark_industry_id
        FROM analysis_composites.industry_corr_benchmark_offsets
      ) s
      GROUP BY benchmark_code
    )
    SELECT
      b.benchmark_code,
      (
        SELECT COALESCE(NULLIF(MAX(sc.industry_label), ''), b.benchmark_code)
        FROM stats.sec_classification sc
        WHERE sc.type = 'index' AND sc.code = b.benchmark_code
      ) AS benchmark_label,
      c.n_industries,
      b.first_start_date,
      b.last_start_date
    FROM bounds b
    JOIN counts c USING (benchmark_code)
    ORDER BY b.benchmark_code
