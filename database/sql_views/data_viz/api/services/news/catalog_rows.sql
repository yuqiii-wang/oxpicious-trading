-- News page — full unified-model classification catalog: one row per
-- DISTINCT (sector, industry, is_industry_not_strategy) of
-- stats.sec_classification, labels decoded with the COALESCE defaults.
-- Covers industry-primary AND strategy-primary rows; drives the
-- sector → industry tree and the L1-only scope resolution.
-- Serves GET /api/news/themes + /api/news/strategy-themes (cached).
-- args: none
      SELECT DISTINCT ON (sector_id, industry_id, is_industry_not_strategy)
             COALESCE(sector_id,      'OTHER') AS sector_id,
             COALESCE(sector_label,   '其他')  AS sector_label,
             COALESCE(industry_id,    'OTHER') AS industry_id,
             COALESCE(industry_label, '未分类') AS industry_label,
             COALESCE(industry_slug,  'other') AS industry_slug,
             COALESCE(is_industry_not_strategy, TRUE) AS is_industry_not_strategy
        FROM stats.sec_classification
       ORDER BY sector_id, industry_id, is_industry_not_strategy
