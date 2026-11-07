-- ONE classification row per code from stats.sec_classification (the UI
-- nav trees' shared meta source, TTL-cached in-process). DISTINCT ON (code)
-- picks the "best" row per code for stocks with MULTIPLE rows (one per
-- qualifying parent index): the parent_index_is_primary row first, then
-- the highest parent_index_weight. n_days comes from the precomputed
-- sec_classification column — no live aggregation over the daily tables.
-- Serves the shared classification consumers (index/stock/etf meta
-- services, analysis classification-universe / nav trees).
-- args:
--   sec_type: value text — the sec_type label ('index'|'etf'|'stock')
      SELECT DISTINCT ON (sc.code)
             sc.code,
             COALESCE(sc.name, '')                  AS name,
             COALESCE(sc.n_days, 0)                 AS n_days,
             sc.first_date::text                    AS first_date,
             sc.last_date::text                     AS last_date,
             COALESCE(sc.sector_id,       'OTHER')  AS sector_id,
             COALESCE(sc.sector_label,    '其他')   AS sector_label,
             COALESCE(sc.industry_id,     'OTHER')  AS industry_id,
             COALESCE(sc.industry_label,  '未分类')  AS industry_label,
             COALESCE(sc.industry_slug,   'other')  AS industry_slug,
             COALESCE(sc.is_industry_not_strategy, TRUE) AS is_industry_not_strategy,
             COALESCE(sc.exchange, '')               AS exchange,
             COALESCE(sc.is_dummy, false)            AS is_dummy
        FROM stats.sec_classification sc
       WHERE sc.type = {{sec_type}}
         AND sc.is_active = TRUE
       ORDER BY sc.code,
                sc.parent_index_is_primary DESC NULLS LAST,
                sc.parent_index_weight DESC NULLS LAST,
                sc.parent_index_code
