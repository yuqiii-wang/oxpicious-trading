-- Benchmark Attribution — ALL industries' benchmark_shared_weight for ONE
-- (benchmark_code, date), each bar = one industry; industry/sector labels
-- joined from stats.sec_classification, benchmark_return computed
-- on-the-fly via a LATERAL prev-day close join so the chart can show
-- Contribution = benchmark_return × (benchmark_shared_weight / 100).
-- target_date NULL falls back to the latest materialized date for the
-- benchmark. Drives the industry-level bar chart in "Benchmark
-- Attribution" mode.
-- Serves GET /api/analysis/industry-attribution/all-industries.
-- args:
--   benchmark_code: value text — the benchmark index code
--   target_date_param: fragment — the raw `$2::date` placeholder. The
--     original SQL numbered the date as the LAST pg param while it
--     appears FIRST in the text, but the loader numbers value slots by
--     first text use — so the placeholder is carried verbatim and the
--     service binds the params array itself
    WITH target_date AS (
      SELECT COALESCE(
        {{target_date_param}},
        (SELECT MAX(date) FROM analysis.industry_attributions
         WHERE benchmark_code = {{benchmark_code}}::text)
      ) AS max_date
    )
    SELECT
      ia.industry_id,
      sc.industry_label,
      sc.sector_label,
      ia.benchmark_shared_weight,
      ia.industry_shared_weight,
      ii.name AS benchmark_name,
      sit.is_broad_market,
      ld.max_date AS date,
      (ib.close - pb.close) / NULLIF(pb.close, 0) AS benchmark_return
    FROM analysis.industry_attributions ia
    CROSS JOIN target_date ld
    LEFT JOIN LATERAL (
      SELECT industry_label, sector_label
      FROM stats.sec_classification
      WHERE industry_id = ia.industry_id AND type = 'index'
        AND industry_label IS NOT NULL
      LIMIT 1
    ) sc ON true
    LEFT JOIN LATERAL (
      SELECT DISTINCT ON (code) name
      FROM stats.index_identity
      WHERE code = ia.benchmark_code
      ORDER BY code, date DESC
    ) ii ON true
    LEFT JOIN LATERAL (
      SELECT BOOL_OR(is_broad_market) AS is_broad_market
      FROM stats.sec_index_tags
      WHERE code = ia.benchmark_code
    ) sit ON true
    LEFT JOIN LATERAL (
      SELECT close FROM stats.index_basic_stats
      WHERE code = ia.benchmark_code AND date = ld.max_date
    ) ib ON true
    LEFT JOIN LATERAL (
      SELECT close FROM stats.index_basic_stats
      WHERE code = ia.benchmark_code AND date < ld.max_date
      ORDER BY date DESC LIMIT 1
    ) pb ON true
    WHERE ia.benchmark_code = {{benchmark_code}}::text
      AND ia.date = ld.max_date
    ORDER BY ia.benchmark_shared_weight DESC NULLS LAST
