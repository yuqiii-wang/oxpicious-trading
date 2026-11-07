-- Latest holdings snapshot for ONE (code, source_type) with per-stock
-- L1/L2 classification. For ETFs (source_type='etf') the DB stores codes
-- WITH exchange suffix, so the suffix is stripped DB-side to match the bare
-- input (code variants); indices (source_type='index') store bare codes —
-- matched directly. The latest subquery and the outer WHERE must agree on
-- the snapshot choice; the optional seasonal constraint (both date_filter
-- fragments) restricts the snapshot to the CALENDAR QUARTER containing the
-- date ("by season" lookup — a quarter without a snapshot yields no data).
-- Serves GET /api/sec-composition (pie chart).
-- args:
--   code_pred: fragment — "h.code = ANY($1::text[])" (outer WHERE)
--   latest_code_pred: fragment — "code = ANY($1::text[])" (latest subquery)
--   date_filter: fragment — optional "AND snapshot_date >=
--     date_trunc('quarter', $3::date) ..." quarter bound (latest subquery)
--   outer_date_filter: fragment — same bound with the h. alias (outer WHERE)
--   (params stay positional, bound by the service:
--    $1 = codes, $2 = source_type, $3 = date when the quarter bound is on)
    SELECT h.snapshot_date,
           h.code,
           h.stock_code,
           h.stock_name,
           h.weight_pct,
           COALESCE(sc.industry_label, '未分类')  AS industry,
           COALESCE(sc.sector_id, 'OTHER')        AS sector_id,
           COALESCE(sc.sector_label, '其他')      AS sector_label
      FROM stats.sec_composition h
      CROSS JOIN (
        SELECT MAX(snapshot_date) AS snap_date
          FROM stats.sec_composition
         WHERE source_type = $2
           AND {{latest_code_pred}}
           {{date_filter}}
      ) latest
      LEFT JOIN LATERAL (
        SELECT sc.sector_id, sc.industry_id, sc.sector_label, sc.industry_label
          FROM stats.sec_classification sc
         WHERE sc.code = h.stock_code AND sc.type = 'stock'
         ORDER BY sc.parent_index_weight DESC NULLS LAST, sc.parent_index_code
         LIMIT 1
      ) sc ON true
     WHERE h.source_type = $2
       AND {{code_pred}}
       AND h.snapshot_date = latest.snap_date
       {{outer_date_filter}}
     ORDER BY h.weight_pct DESC
