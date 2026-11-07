-- MA-Spread classification meta rows — one row per code WITH analysis rows
-- (from the precomputed per-code summary table analysis.mov_ave_spreads_codes)
-- LEFT JOINed with its precomputed L1/L2 classification from
-- stats.sec_classification. is_industry_not_strategy distinguishes
-- industry-primary (TRUE) from strategy-primary (FALSE) rows. Shared by the
-- codes filter maps and both ThemeSelector trees.
-- Serves GET /api/analysis/mov-ave-spread/codes, /mov-ave-spread/themes,
-- /mov-ave-spread/strategy-themes.
-- args:
--   sec_type: value int — static.label_dim sec_type SMALLINT code (secTypeParam)
--   m_type: value text — sec_classification type discriminator (MA_SPREAD_META_TYPE)
  WITH spread_codes AS (
    SELECT code
    FROM analysis.mov_ave_spreads_codes
    WHERE sec_type = {{sec_type}}
  )
  SELECT
    sc.code,
    COALESCE(m.name, '')             AS name,
    COALESCE(m.sector_id,       'OTHER')  AS sector_id,
    COALESCE(m.sector_label,    '其他')   AS sector_label,
    COALESCE(m.industry_id,     'OTHER')  AS industry_id,
    COALESCE(m.industry_label,  '未分类') AS industry_label,
    COALESCE(m.industry_slug,   'other')  AS industry_slug,
    COALESCE(m.is_industry_not_strategy, TRUE) AS is_industry_not_strategy,
    COALESCE(m.exchange, '')               AS exchange
  FROM spread_codes sc
  LEFT JOIN stats.sec_classification m ON m.code = sc.code AND m.type = {{m_type}}::text
  WHERE COALESCE(m.is_active, TRUE) = TRUE
