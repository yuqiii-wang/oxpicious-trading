-- Live-data meta — one row per ACTIVE code of the requested type with at
-- least one intraday bar, with the L1/L2 taxonomy from
-- stats.sec_classification. Drives from the small classification table and
-- probes the intraday table per code via JOIN LATERAL ... LIMIT 1 (each
-- probe is partition-pruned + stops at the first PK index hit; a correlated
-- EXISTS was rewritten by the planner into a merge semi-join — LATERAL
-- LIMIT 1 forces the per-code probe shape). DISTINCT ON (code) picks ONE
-- row per code (stocks may carry one sec_classification row per qualifying
-- parent): parent_index_is_primary TRUE first, then highest weight; indices
-- and ETFs already have a single row per code. n_dates/first/last come from
-- the PRECOMPUTED sec_classification columns (consumers only use them for
-- ordering). Serves GET /api/live-data (themes + combined, cached).
-- args:
--   sec_type: value text — 'index' | 'stock'
--   intraday_table: fragment — TABLES[sec_type].table
--     (stats.index_intraday_5min / stats.stock_intraday_5min)
    SELECT y.code, y.name, y.sector_id, y.sector_label,
           y.industry_id, y.industry_label, y.industry_slug,
           y.is_industry_not_strategy, y.exchange,
           y.n_dates, y.first_date, y.last_date
      FROM (
        SELECT DISTINCT ON (sc.code)
               sc.code,
               COALESCE(sc.name, '') AS name,
               COALESCE(sc.sector_id,       'OTHER')  AS sector_id,
               COALESCE(sc.sector_label,    '其他')   AS sector_label,
               COALESCE(sc.industry_id,     'OTHER')  AS industry_id,
               COALESCE(sc.industry_label,  '未分类')  AS industry_label,
               COALESCE(sc.industry_slug,   'other')  AS industry_slug,
               COALESCE(sc.is_industry_not_strategy, TRUE) AS is_industry_not_strategy,
               COALESCE(sc.exchange, '')               AS exchange,
               COALESCE(sc.n_days, 0)                  AS n_dates,
               sc.first_date::text                     AS first_date,
               sc.last_date::text                      AS last_date
          FROM stats.sec_classification sc
          JOIN LATERAL (
            SELECT 1
              FROM {{intraday_table}} t
             WHERE t.code = sc.code
             LIMIT 1
          ) ex ON true
         WHERE sc.type = {{sec_type}}
           AND sc.is_active = TRUE
         ORDER BY sc.code,
                  sc.parent_index_is_primary DESC NULLS LAST,
                  sc.parent_index_weight DESC NULLS LAST,
                  sc.parent_index_code
      ) y
     ORDER BY y.n_dates DESC, y.code
