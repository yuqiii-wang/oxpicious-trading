-- Intraday Movements — fallback member index % change per (code, tick):
-- pcts computed from the RAW intraday table (same universe/prev-close
-- semantics as the industry fallback). Drives the bottom plot when live
-- tick rows are missing for the target date.
-- Serves GET /api/live-data/intraday-movements
-- args:
--   benchmark_code: value text — the benchmark index code
--   date: value date — the target trading date
WITH cls AS (
    SELECT DISTINCT ON (code)
        code,
        industry_id
    FROM stats.sec_classification
    WHERE is_active = TRUE
      AND industry_id IS NOT NULL AND industry_id <> ''
    ORDER BY code
),
universe AS (
    SELECT sc.code, cls.industry_id
    FROM stats.sec_classification sc
    JOIN cls ON cls.code = sc.code
    WHERE sc.is_active = TRUE
      AND sc.type IN ('index', 'etf')
      AND cls.industry_id NOT LIKE 'BROAD_%'
      AND sc.code <> {{benchmark_code}}::text
),
prev_d AS (
    SELECT MAX(date) AS d
    FROM stats.index_basic_stats
    WHERE date < {{date}}::date AND close IS NOT NULL
),
member_prev_close AS (
    SELECT b.code, b.close AS prev_close
    FROM stats.index_basic_stats b
    WHERE b.date = (SELECT d FROM prev_d)
      AND b.close IS NOT NULL AND b.close::text <> 'NaN'
      AND b.code IN (SELECT code FROM universe)
)
SELECT
    i5.time::text                     AS time,
    i5.code,
    COALESCE(ii.name, i5.code)        AS code_name,
    u.industry_id,
    (i5.close / mp.prev_close - 1)::float8 AS code_price_pct
FROM stats.index_intraday_5min i5
JOIN universe u ON u.code = i5.code
JOIN member_prev_close mp ON mp.code = i5.code
LEFT JOIN LATERAL (
    SELECT name FROM stats.index_identity
    WHERE code = i5.code ORDER BY date DESC LIMIT 1
) ii ON true
WHERE i5.date = {{date}}::date
  AND i5.close IS NOT NULL AND i5.close::text <> 'NaN'
ORDER BY i5.time, u.industry_id, i5.code
