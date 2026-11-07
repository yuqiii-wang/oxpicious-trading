-- Margin Trends — securities per industry for the 'etf' attribution: ETF
-- codes from stats.sec_classification (parent_index_is_primary).
-- Serves GET /api/analysis/margin-trends/themes
-- args: (none)
  SELECT sc.industry_id,
         sc.code,
         COALESCE(NULLIF(sc.name, ''), sc.code) AS name
  FROM stats.sec_classification sc
  WHERE sc.type = 'etf'
    AND sc.parent_index_is_primary = TRUE
    AND sc.is_active = TRUE
    AND sc.industry_id IS NOT NULL AND sc.industry_id <> ''
  ORDER BY sc.code
