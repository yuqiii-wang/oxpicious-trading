-- Margin Trends 'index' attribution — the industry's member index codes
-- (distinct index_code from analysis.margin_index_series) with their
-- sec_classification name labels.
-- Serves GET /api/analysis/margin-trends/industry-series?attribution=index
-- args:
--   industry_id: value text — the industry to list securities for
  SELECT DISTINCT s.index_code AS code,
         COALESCE(NULLIF(sc.name, ''), s.index_code) AS name
  FROM analysis.margin_index_series s
  LEFT JOIN LATERAL (
    SELECT name FROM stats.sec_classification
    WHERE code = s.index_code AND type = 'index' AND is_active = TRUE
    LIMIT 1
  ) sc ON TRUE
  WHERE s.industry_id = {{industry_id}}::text
  ORDER BY code
