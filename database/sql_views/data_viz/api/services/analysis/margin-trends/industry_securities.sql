-- Margin Trends 'industry' attribution — the industry's own (code,
-- label) row from analysis.margin_industry_stats (ONE row; the label
-- rides the series query's source table).
-- Serves GET /api/analysis/margin-trends/industry-series?attribution=industry
-- args:
--   industry_id: value text — the industry to look up
  SELECT industry_id AS code,
         COALESCE(NULLIF(industry_label, ''), industry_id) AS name
  FROM analysis.margin_industry_stats
  WHERE industry_id = {{industry_id}}::text
  LIMIT 1
