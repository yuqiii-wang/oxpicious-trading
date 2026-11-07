-- Margin Trends — one row per industry that has rows in
-- analysis.margin_industry_stats, JOINed with stats.sec_classification
-- (type='index') to recover sector_id / sector_label / industry_slug /
-- is_industry_not_strategy.
-- Serves GET /api/analysis/margin-trends/themes and
--   GET /api/analysis/margin-trends/strategy-themes
-- args: (none)
  WITH margin_industries AS (
    SELECT DISTINCT industry_id, industry_label
    FROM analysis.margin_industry_stats
  )
  SELECT
    mi.industry_id,
    COALESCE(NULLIF(mi.industry_label, ''), mi.industry_id) AS industry_label,
    COALESCE(sc.sector_id,              'OTHER')  AS sector_id,
    COALESCE(sc.sector_label,           '其他')   AS sector_label,
    COALESCE(sc.industry_slug,          LOWER(mi.industry_id)) AS industry_slug,
    COALESCE(sc.is_industry_not_strategy, TRUE)  AS is_industry_not_strategy
  FROM margin_industries mi
  LEFT JOIN LATERAL (
    SELECT sector_id, sector_label, industry_slug, is_industry_not_strategy
    FROM stats.sec_classification
    WHERE industry_id = mi.industry_id
      AND type = 'index'
      AND is_active = TRUE
    LIMIT 1
  ) sc ON TRUE
