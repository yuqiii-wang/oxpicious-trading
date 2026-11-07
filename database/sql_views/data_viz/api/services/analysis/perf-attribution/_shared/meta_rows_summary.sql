-- Perf-attr classification meta rows ('summary' codeSource) — one row per
-- code in the cross_stats_code_summary rollup (filtered by sec_type) with
-- its precomputed L1/L2 classification from stats.sec_classification.
-- is_industry_not_strategy distinguishes industry-primary (TRUE) from
-- strategy-primary (FALSE) rows. Shared by the codes list's classification
-- filter and both themes trees.
-- Serves GET /api/analysis/perf-attr/codes, /perf-attr/themes,
-- /perf-attr/strategy-themes.
-- args:
--   sec_type: value text — cross_stats sec_type
--   meta_type: value text — sec_classification type discriminator
--   meta_table: fragment — PERF_ATTR_META_TABLE[sec_type] (stats.sec_classification)
    WITH perf_codes AS (
      SELECT code
      FROM stats.cross_stats_code_summary
      WHERE sec_type = {{sec_type}}::text)
    SELECT
      pc.code,
      COALESCE(m.name, '')             AS name,
      COALESCE(m.sector_id,       'OTHER')  AS sector_id,
      COALESCE(m.sector_label,    '其他')   AS sector_label,
      COALESCE(m.industry_id,     'OTHER')  AS industry_id,
      COALESCE(m.industry_label,  '未分类') AS industry_label,
      COALESCE(m.industry_slug,   'other')  AS industry_slug,
      COALESCE(m.is_industry_not_strategy, TRUE) AS is_industry_not_strategy
    FROM perf_codes pc
    LEFT JOIN {{meta_table}} m ON m.code = pc.code AND m.type = {{meta_type}}::text
