-- PE & Dividend valuation meta rows — the analysis.pe_codes code universe
-- LEFT JOINed with stats.sec_classification (a multi-parent stock yields
-- one row per parent — the tree builders consolidate). Cached per sec_type
-- for the shared 10-min TTL; exchange + classification filters are applied
-- client-side, so the rows are exchange-independent and safely cacheable.
-- args (listed in the original $n order — the {{...}} refs at the end of
-- these lines pin pg param numbering to the original query's $1/$2 because
-- the JOIN's $2 appears BEFORE the WHERE's $1 in the query text and the
-- loader numbers slots by first appearance; removing the refs only
-- renumbers, never rebinds):
--   sec_type: value smallint — static.label_dim 'sec_type' code — {{sec_type}}
--   meta_type: value text — sec_classification type discriminator — {{meta_type}}
  SELECT
    c.code,
    COALESCE(c.name, '')             AS name,
    COALESCE(m.sector_id,       'OTHER')  AS sector_id,
    COALESCE(m.sector_label,    '其他')   AS sector_label,
    COALESCE(m.industry_id,     'OTHER')  AS industry_id,
    COALESCE(m.industry_label,  '未分类') AS industry_label,
    COALESCE(m.industry_slug,   'other')  AS industry_slug,
    COALESCE(m.is_industry_not_strategy, TRUE) AS is_industry_not_strategy,
    COALESCE(m.exchange, '')               AS exchange
  FROM analysis.pe_codes c
  LEFT JOIN stats.sec_classification m ON m.code = c.code AND m.type = {{meta_type}}::text
  WHERE c.sec_type = {{sec_type}}  -- static.label_dim 'sec_type' code
    AND COALESCE(m.is_active, TRUE) = TRUE
