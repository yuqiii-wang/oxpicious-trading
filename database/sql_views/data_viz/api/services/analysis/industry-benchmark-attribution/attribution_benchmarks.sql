-- Benchmark Attribution — the distinct BROAD-MARKET benchmark codes in
-- analysis.industry_attributions, enriched with display name. Only 宽基
-- benchmarks are returned (drives the 1st-plot dropdown); member-index
-- benchmarks appear automatically as bars in the per-industry chart.
-- Serves GET /api/analysis/industry-attribution/benchmarks.
-- args: none
    WITH bench_codes AS (
      SELECT DISTINCT ia.benchmark_code
      FROM analysis.industry_attributions ia
      JOIN stats.sec_index_tags sit ON sit.code = ia.benchmark_code
      WHERE sit.is_broad_market = TRUE
    )
    SELECT
      bc.benchmark_code,
      ii.name AS benchmark_name,
      TRUE AS is_broad_market
    FROM bench_codes bc
    LEFT JOIN LATERAL (
      SELECT DISTINCT ON (code) name
      FROM stats.index_identity
      WHERE code = bc.benchmark_code
      ORDER BY code, date DESC
    ) ii ON true
    ORDER BY bc.benchmark_code
