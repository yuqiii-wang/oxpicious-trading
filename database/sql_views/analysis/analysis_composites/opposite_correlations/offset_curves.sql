-- Consolidated per-industry offset curves for ONE benchmark (stats.
-- cross_stats code_price_with_benchmark_offset averaged over each
-- industry's member indices per pool bucket; pools mirror the
-- builds.industry buckets + the 'all' pool) — the opposite-corr offsets
-- analysis' step-2 load.
-- args:
--   benchmark_code: value text — the benchmark index code
--   extra_where: fragment — the optional filter predicates built by the
--     caller ("WHERE mp.industry_id = ANY($2)" / "AND cs.date >= $3"
--     compositions, "" when unfiltered); the $n references inside are
--     bound positionally by the caller right after the benchmark param
WITH latest AS (
    SELECT code, MAX(snapshot_date) AS max_date
    FROM stats.sec_composition
    WHERE source_type = 'index' AND stock_code IS NOT NULL
    GROUP BY code
),
stock_num AS (
    SELECT h.code, COUNT(DISTINCT h.stock_code) AS n
    FROM stats.sec_composition h
    JOIN latest ld ON h.code = ld.code AND h.snapshot_date = ld.max_date
    WHERE h.source_type = 'index' AND h.stock_code IS NOT NULL
    GROUP BY h.code
),
cls AS (
    -- EVERY classified industry, including strategy themes (DIV_GENERAL
    -- 红利 etc.): the baseline table (stats.industry_basic_stats) carries
    -- them, so restricting the offset curves to genuine industries left
    -- their pairs' offset_sub_corr_* eternally NULL — half-populated
    -- audit rows (2026-10-17). The offset math (member close-change
    -- minus benchmark change, averaged + cumulated) is
    -- classification-agnostic.
    SELECT code, industry_id
    FROM stats.sec_classification
    WHERE type = 'index'
      AND is_active = TRUE
      AND industry_id IS NOT NULL AND industry_id <> ''
),
member_pool AS (
    -- Pool buckets mirror builds.industry.classify_pool_vectorized
    -- (static.label_dim SMALLINT codes: 0 small / 1 mid / 2 large):
    -- small < 51 constituent stocks, mid <= 180, large otherwise;
    -- compositionless members contribute to the 'all' (3) pool only.
    SELECT cls.code, cls.industry_id,
           -- width_bucket over the thresholds [51, 181): 0 = small (< 51),
           -- 1 = mid (< 181), 2 = large; NULL n (compositionless member)
           -- stays NULL. Mirrors
           -- builds.industry.classify_pool_vectorized.
           width_bucket(sn.n, ARRAY[51, 181])::smallint AS bucket
    FROM cls cls
    LEFT JOIN stock_num sn ON sn.code = cls.code
),
pools(pool_size) AS (
    VALUES (0::smallint), (1::smallint), (2::smallint), (3::smallint)
)
SELECT mp.industry_id, p.pool_size,
       extract(epoch from cs.date)::float8 AS date,
       AVG(cs.code_price_with_benchmark_offset)::float8 AS offset_pts
FROM member_pool mp
JOIN stats.cross_stats cs
    ON cs.code = mp.code
   AND cs.benchmark_code = {{benchmark_code}}::text
   AND cs.sec_type = 'index'
   AND cs.code_price_with_benchmark_offset IS NOT NULL
JOIN pools p ON p.pool_size = 3 OR p.pool_size = mp.bucket
{{extra_where}}
GROUP BY mp.industry_id, p.pool_size, cs.date
