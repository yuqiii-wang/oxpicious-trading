-- Quarterly composition — per-season industry-aggregated weights for the
-- ETF Holdings page's stacked bar chart. For each calendar quarter the
-- LATEST snapshot within that quarter is chosen (DISTINCT ON
-- date_trunc('quarter', snapshot_date)); quarters without a snapshot are
-- simply absent (no carry-forward). Weights aggregate per industry via the
-- same LATERAL sec_classification join as holdings.sql.
-- Serves GET /api/sec-composition/quarterly.
-- args:
--   code_pred: fragment — "h.code = ANY($1::text[])" (the join predicate;
--     the snaps CTE carries the same predicate inline)
--   (params stay positional, bound by the service:
--    $1 = codes, $2 = source_type)
    WITH snaps AS (
      SELECT DISTINCT ON (date_trunc('quarter', snapshot_date))
             date_trunc('quarter', snapshot_date) AS quarter_start,
             snapshot_date
        FROM stats.sec_composition
       WHERE source_type = $2
         AND code = ANY($1::text[])
       ORDER BY date_trunc('quarter', snapshot_date), snapshot_date DESC
    )
    SELECT s.quarter_start::text AS quarter_start,
           s.snapshot_date::text AS snapshot_date,
           COALESCE(sc.industry_id, '')        AS industry_id,
           COALESCE(sc.sector_id, 'OTHER')   AS sector_id,
           COALESCE(sc.sector_label, '其他') AS sector_label,
           COALESCE(sc.industry_label, '未分类') AS industry,
           SUM(h.weight_pct)                AS weight_pct,
           COUNT(*)                         AS n_holdings
      FROM snaps s
      JOIN stats.sec_composition h
        ON h.source_type = $2
       AND h.snapshot_date = s.snapshot_date
       AND {{code_pred}}
      LEFT JOIN LATERAL (
        SELECT sc.sector_id, sc.industry_id, sc.sector_label, sc.industry_label
          FROM stats.sec_classification sc
         WHERE sc.code = h.stock_code AND sc.type = 'stock'
         ORDER BY sc.parent_index_weight DESC NULLS LAST, sc.parent_index_code
         LIMIT 1
      ) sc ON true
     GROUP BY s.quarter_start, s.snapshot_date,
              COALESCE(sc.industry_id, ''),
              COALESCE(sc.sector_id, 'OTHER'),
              COALESCE(sc.sector_label, '其他'),
              COALESCE(sc.industry_label, '未分类')
     ORDER BY s.quarter_start, SUM(h.weight_pct) DESC
