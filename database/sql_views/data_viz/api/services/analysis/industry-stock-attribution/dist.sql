-- Industry Stock Attribution — the 12-bucket daily pct-change
-- distribution for ONE (industry_id, date): one row per
-- static.label_dim 'stock_change_bucket' code with stock_count and the
-- denormalized total_stocks, plus the 90 most recent available dates
-- (jsonb array, feeds the view's date dropdown). target_date NULL
-- falls back to the latest materialized date for the industry. Drives
-- the per-industry histograms in "Stock Attribution" mode on the
-- Market Beta page.
-- Serves GET /api/analysis/industry-stock-attribution.
-- args:
--   industry_id: value text — the industry whose distribution to fetch
--   date_param: fragment — the raw `$2::date` placeholder; the service
--     binds the params array itself ([industry_id, date | null]) —
--     same numbering-by-first-text-use pattern as the etf-bars view
--   bucket_decode: fragment — the label_dim decode subquery for the
--     bucket code column (labelDecodeSql('stock_change_bucket', r.bucket))
WITH target_date AS (
    SELECT COALESCE(
        {{date_param}},
        (SELECT MAX(date) FROM analysis.industry_stock_change_dist
         WHERE industry_id = {{industry_id}}::text)
    ) AS d
),
dist_rows AS (
    SELECT r.date, r.bucket, r.stock_count, r.total_stocks, r.industry_label
    FROM analysis.industry_stock_change_dist r
    CROSS JOIN target_date td
    WHERE r.industry_id = {{industry_id}}::text
      AND r.date = td.d
)
SELECT
    r.date,
    r.bucket,
    {{bucket_decode}} AS bucket_label,
    r.stock_count,
    r.total_stocks,
    r.industry_label,
    (SELECT jsonb_agg(dd ORDER BY dd DESC)
     FROM (SELECT DISTINCT date AS dd
           FROM analysis.industry_stock_change_dist
           WHERE industry_id = {{industry_id}}::text
           ORDER BY dd DESC
           LIMIT 90) dates) AS available_dates
FROM dist_rows r
ORDER BY r.bucket
