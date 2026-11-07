-- ETF-margin browse-list meta — one row per ETF code with the precomputed
-- L1/L2 classification from stats.sec_classification (type='etf').
-- Ordering (high → low priority): has_margin DESC (margin data first),
-- n_days DESC (longer history first), v.code (stable tiebreaker).
-- HAVING COUNT(v.date) >= 40 — ETFs with fewer than 40 trading days of rows
-- in v_etf_margin are suppressed from the data-viz list (matches the
-- "Insufficient data" alert in EtfMarginPanel); the live row count from the
-- view, not the precomputed .n_days (may be stale). Labels are
-- DENORMALIZED onto sec_classification by build_classification.py — no
-- catalog JOIN. Serves GET /api/etf-margin (themes + combined).
-- args: (none)
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
   GROUP BY v.code
  HAVING COUNT(v.date) >= 40
     AND COALESCE(BOOL_OR(m.is_active), TRUE)
   ORDER BY has_margin DESC, n_days DESC, v.code
