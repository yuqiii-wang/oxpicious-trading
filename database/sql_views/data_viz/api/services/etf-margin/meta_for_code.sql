-- Code-lookup variant of the etf-margin meta (exact-code search, the
-- CodeSearchBar) — same aggregate as meta.sql but WITHOUT the
-- `HAVING COUNT(v.date) >= 40` threshold: newly-listed ETFs (e.g. 159066
-- with ~20 trading days) must stay searchable; the browse-list threshold
-- and the search path are intentionally decoupled. Uncached.
-- args:
--   codes: value text[] — the exact ETF code(s) to fetch (bare, single-element)
  SELECT v.code,
         MAX(v.name) AS name,
         COALESCE(MAX(m.sector_id),       'OTHER')  AS sector_id,
         COALESCE(MAX(m.sector_label),    '其他')   AS sector_label,
         COALESCE(MAX(m.industry_id),     'OTHER')  AS industry_id,
         COALESCE(MAX(m.industry_label),  '未分类')  AS industry_label,
         COALESCE(MAX(m.industry_slug),   'other')  AS industry_slug,
         COALESCE(BOOL_OR(m.is_industry_not_strategy), TRUE) AS is_industry_not_strategy,
         COALESCE(MAX(m.parent_index_code), '')     AS index_code,
         COALESCE(MAX(mi.name), '')                  AS index_name,
         COALESCE(MAX(m.exchange), '')               AS exchange,
         COALESCE(BOOL_OR(m.has_margin), FALSE)      AS has_margin,
         COUNT(v.date)                               AS n_days
    FROM stats.v_etf_margin v
    LEFT JOIN stats.sec_classification m ON v.code = m.code AND m.type = 'etf'
    LEFT JOIN stats.sec_classification mi ON mi.code = m.parent_index_code AND mi.type = 'index'
   WHERE v.code = ANY({{codes}}::text[])
   GROUP BY v.code
