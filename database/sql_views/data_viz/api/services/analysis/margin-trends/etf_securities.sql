-- Margin Trends 'etf' attribution — the industry's ETF codes from
-- stats.sec_classification (parent_index_is_primary) with name labels.
-- Serves GET /api/analysis/margin-trends/industry-series?attribution=etf
-- args:
--   industry_id: value text — the industry to list securities for
  SELECT sc.code, COALESCE(NULLIF(sc.name, ''), sc.code) AS name
  FROM stats.sec_classification sc
  WHERE sc.type = 'etf'
    AND sc.industry_id = {{industry_id}}::text
    AND sc.parent_index_is_primary = TRUE
    AND sc.is_active = TRUE
  ORDER BY sc.code
