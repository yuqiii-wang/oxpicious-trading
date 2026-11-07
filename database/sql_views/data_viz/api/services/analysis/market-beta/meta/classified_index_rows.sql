-- Market-beta classification meta — one row per classified index
-- (type='index') that has at least one stats.sec_composition snapshot.
-- Used by listMarketBetaStrategyThemes() (RIGHT column strategy/theme tree)
-- and by getMarketBetaChart() for classification filtering.
-- is_industry_not_strategy distinguishes industry-primary (TRUE) from
-- strategy-primary (FALSE) rows; for strategy-primary rows sector_id/
-- industry_id carry the STRATEGY/theme classification.
-- Serves GET /api/analysis/market-beta themes + strategy-themes.
-- args: none
  SELECT sc.code,
         COALESCE(sc.name, '')             AS name,
         sc.exchange                       AS exchange,
         COALESCE(sc.sector_id,       'OTHER')  AS sector_id,
         COALESCE(sc.sector_label,    '其他')   AS sector_label,
         COALESCE(sc.industry_id,     'OTHER')  AS industry_id,
         COALESCE(sc.industry_label,  '未分类')  AS industry_label,
         COALESCE(sc.industry_slug,   'other')  AS industry_slug,
         COALESCE(sc.is_industry_not_strategy, TRUE) AS is_industry_not_strategy
    FROM stats.sec_classification sc
   WHERE sc.type = 'index'
     AND sc.is_active = TRUE
     AND sc.industry_id IS NOT NULL
     AND sc.industry_id <> ''
     AND COALESCE(sc.sector_id, '') <> 'DEBT'
     AND EXISTS (
         SELECT 1 FROM stats.sec_composition sc2
         WHERE sc2.code = sc.code AND sc2.source_type = 'index'
     )
