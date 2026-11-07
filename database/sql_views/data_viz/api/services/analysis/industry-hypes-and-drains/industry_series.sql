-- Hypes & Drains — DAILY non-this-industry price level + benchmark
-- shared weight for ALL ranked industries of ONE attribution type (the
-- service compounds the daily decomposition identity into the
-- trailing-N-day shared return; each (industry, benchmark, date) has TWO
-- rows, one per attribution_type, and mixing them would corrupt the
-- consecutive-day returns). Rows missing the non-industry price level
-- are excluded.
-- Serves GET /api/analysis/industry-hypes-and-drains.
-- args:
--   industry_ids: value text[] — the ranked industries' ids
--   benchmark_code: value text — the benchmark index code
--   attribution_type: value text — 'equal' | 'trading_amt' (matches the
--     requested weighting)
    SELECT
        ia.date,
        ia.industry_id,
        ia.benchmark_non_this_industry_price AS non_ind_price,
        ia.benchmark_shared_weight
    FROM analysis.industry_attributions ia
    WHERE ia.industry_id = ANY({{industry_ids}}::text[])
      AND ia.benchmark_code = {{benchmark_code}}::text
      AND ia.attribution_type = {{attribution_type}}::text
      AND ia.benchmark_non_this_industry_price IS NOT NULL
    ORDER BY ia.industry_id, ia.date
