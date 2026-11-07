-- Industry-attributions step preview — distinct industries + benchmarks
-- that will appear in the broad-market output. The source is already at
-- industry grain (code = industry_id), so no GROUP BY is needed.
-- Restricted to broad-market benchmarks — only these are materialized in
-- industry_attributions.
-- args: none
    SELECT
        COUNT(DISTINCT cs.code) AS n_industries,
        COUNT(DISTINCT cs.benchmark_code) AS n_benchmarks
    FROM stats.cross_stats cs
    WHERE cs.sec_type = 'industry'
      AND cs.code_sec_shared_weight IS NOT NULL
      AND cs.benchmark_code IN (
          SELECT code FROM stats.sec_index_tags WHERE is_broad_market = TRUE
      )
