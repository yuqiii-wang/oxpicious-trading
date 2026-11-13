-- Stock attribution step (analysis.market_beta internal pipeline) — the
-- per-stock source rows: (date, industry_id, pct_change) for every stock
-- day joinable to the one-industry-per-stock mapping. The bucket
-- assignment + (industry, date, bucket) counting happens in pandas
-- (cudf whole-frame); industry_label is NOT selected here — label
-- strings must not ride the big frame through groupby/sort (the cuDF
-- 26.08 string fault class); the label is merged onto the SMALL
-- aggregated frame at the write boundary (see industry_labels.sql).
-- args:
--   date_filter: fragment — "" for the full (re)build; incremental passes
--     "AND s.date = ANY($1::date[])" and binds the sorted target-date
--     array as the statement's $1 driver param
WITH stock_map AS (
    SELECT DISTINCT ON (code) code, industry_id
    FROM stats.sec_classification
    WHERE type = 'stock'
    ORDER BY code, parent_index_is_primary DESC,
             parent_index_weight DESC NULLS LAST
)
SELECT
    extract(epoch from s.date)::float8 AS date,
    sm.industry_id,
    s.pct_change::float8 AS pct_change
FROM stats.stock_basic_stats s
JOIN stock_map sm ON sm.code = s.code
WHERE s.pct_change IS NOT NULL
  {{date_filter}}
