-- Benchmark Attribution — benchmark close (rebased to 100 at the first
-- date in the partition via FIRST_VALUE, comparable to the 100-based
-- non_this_industry_rolling_*_price) + the non-this-industry price
-- columns + shared weight for ONE (industry_id, benchmark_code) pair.
-- Drives the green/red shade overlay on the BenchmarkPriceChart; the
-- rolling_Xdays_price columns (5/20/60/120/255/500) are returned as-is
-- and the frontend dropdown picks which one drives the overlay. Aliases
-- strip the `benchmark_` prefix so row keys match the response payload;
-- industry_label / benchmark_name / is_broad_market come from LATERAL
-- LIMIT-1 joins (no row multiplication).
-- Serves GET /api/analysis/industry-attribution/non-this-industry-price.
-- args:
--   industry_id: value text — the industry to price
--   benchmark_code: value text — the benchmark index code
SELECT
        ia.date,
        ib.close AS benchmark_close,
        -- Rebase benchmark close to 100 at the first date in the partition
        -- so it's comparable to non_this_industry_rolling_*_price (also 100-based).
        100.0 * ib.close / NULLIF(
          FIRST_VALUE(ib.close) OVER (
            PARTITION BY ia.benchmark_code
            ORDER BY ia.date
            ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
          ), 0
        ) AS benchmark_rolling,
        ia.benchmark_non_this_industry_price                    AS non_this_industry_price,
        ia.benchmark_non_this_industry_rolling_5days_price      AS non_this_industry_rolling_5days_price,
        ia.benchmark_non_this_industry_rolling_20days_price      AS non_this_industry_rolling_20days_price,
        ia.benchmark_non_this_industry_rolling_60days_price      AS non_this_industry_rolling_60days_price,
        ia.benchmark_non_this_industry_rolling_120days_price     AS non_this_industry_rolling_120days_price,
        ia.benchmark_non_this_industry_rolling_255days_price     AS non_this_industry_rolling_255days_price,
        ia.benchmark_non_this_industry_rolling_500days_price    AS non_this_industry_rolling_500days_price,
        ia.benchmark_shared_weight,
        sc.industry_label,
        ii.name AS benchmark_name,
        sit.is_broad_market
     FROM analysis.industry_attributions ia
     LEFT JOIN stats.index_basic_stats ib
        ON ib.code = ia.benchmark_code AND ib.date = ia.date
     LEFT JOIN LATERAL (
        SELECT industry_label
        FROM stats.sec_classification
        WHERE industry_id = ia.industry_id AND type = 'index'
          AND industry_label IS NOT NULL
        LIMIT 1
     ) sc ON true
     LEFT JOIN LATERAL (
        SELECT DISTINCT ON (code) code, name
        FROM stats.index_identity
        WHERE code = ia.benchmark_code
        ORDER BY code, date DESC
     ) ii ON true
     LEFT JOIN LATERAL (
        SELECT is_broad_market
        FROM stats.sec_index_tags
        WHERE code = ia.benchmark_code
        LIMIT 1
     ) sit ON true
     WHERE ia.industry_id = {{industry_id}}::text
       AND ia.benchmark_code = {{benchmark_code}}::text
     ORDER BY ia.date ASC
