-- Intraday Movements — member index % change per (code, tick, industry)
-- for ALL members with tick rows (index/etf members; stocks carry no
-- ticks by design). Industry identity = stats.sec_classification (latest
-- ACTIVE row per code).
-- Serves GET /api/live-data/intraday-movements
-- args:
--   date: value date — the target trading date
--   benchmark_code: value text — the benchmark index code
WITH cls AS (
    SELECT DISTINCT ON (code)
        code,
        industry_id
    FROM stats.sec_classification
    WHERE is_active = TRUE
      AND industry_id IS NOT NULL AND industry_id <> ''
    ORDER BY code
)
SELECT
    a.time::text                                      AS time,
    a.code,
    COALESCE(ii.name, a.code)                         AS code_name,
    cls.industry_id,
    a.code_price_pct_relative_prev_date_close         AS code_price_pct
FROM live.sec_alloc_live_attribution a
JOIN cls ON cls.code = a.code
LEFT JOIN LATERAL (
    SELECT name FROM stats.index_identity
    WHERE code = a.code ORDER BY date DESC LIMIT 1
) ii ON true
WHERE a.benchmark_code = {{benchmark_code}}::text
  AND a.date = {{date}}::date
ORDER BY a.time, cls.industry_id, a.code
