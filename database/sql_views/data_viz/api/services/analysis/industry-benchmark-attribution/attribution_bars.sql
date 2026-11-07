-- Benchmark Attribution — pre-materialized attribution rows (shared
-- weights) for ONE industry at the target date, one row per benchmark;
-- benchmark_return computed on-the-fly via a LATERAL prev-day close
-- join (not stored in analysis.industry_attributions). target_date NULL
-- falls back to the latest materialized date for the industry. Drives
-- the per-industry attribution bar charts (2nd plot onward) in
-- "Benchmark Attribution" mode.
-- Serves GET /api/analysis/industry-benchmark-attribution.
-- args:
--   industry_id: value text — the industry whose attribution to read
--   target_date_param: fragment — the raw `$2::date` placeholder. The
--     original SQL numbered the date as the LAST pg param while it
--     appears FIRST in the text, but the loader numbers value slots by
--     first text use — so the placeholder is carried verbatim and the
--     service binds the params array itself
    WITH target_date AS (
      SELECT COALESCE(
        {{target_date_param}},
        (SELECT MAX(date) FROM analysis.industry_attributions
         WHERE industry_id = {{industry_id}}::text)
      ) AS max_date
    ),
    bench_rows AS (
      SELECT
        ia.benchmark_code,
        ia.industry_shared_weight,
        ia.benchmark_shared_weight
      FROM analysis.industry_attributions ia
      CROSS JOIN target_date ld
      WHERE ia.industry_id = {{industry_id}}::text
        AND ia.date = ld.max_date
    )
    SELECT
      br.benchmark_code,
      bi.name AS benchmark_name,
      ld.max_date AS date,
      br.industry_shared_weight,
      br.benchmark_shared_weight,
      sit.is_broad_market,
      (ib.close - pb.close) / NULLIF(pb.close, 0) AS benchmark_return
    FROM bench_rows br
    CROSS JOIN target_date ld
    LEFT JOIN LATERAL (
      SELECT DISTINCT ON (code) code, name
      FROM stats.index_identity
      WHERE code = br.benchmark_code
      ORDER BY code, date DESC
    ) bi ON true
    LEFT JOIN LATERAL (
      SELECT close FROM stats.index_basic_stats
      WHERE code = br.benchmark_code AND date = ld.max_date
    ) ib ON true
    LEFT JOIN LATERAL (
      SELECT close FROM stats.index_basic_stats
      WHERE code = br.benchmark_code AND date < ld.max_date
      ORDER BY date DESC LIMIT 1
    ) pb ON true
    LEFT JOIN LATERAL (
      SELECT BOOL_OR(is_broad_market) AS is_broad_market
      FROM stats.sec_index_tags
      WHERE code = br.benchmark_code
    ) sit ON true
    ORDER BY br.benchmark_code
