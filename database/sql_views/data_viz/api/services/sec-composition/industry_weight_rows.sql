-- Industry weight series — ONE industry's weight in a security's
-- composition across ALL snapshot dates (roughly monthly — denser than the
-- quarterly latest-per-quarter view). Per snapshot date: the industry's
-- summed raw weight_pct PLUS the snapshot total (normalization is applied
-- client-side: weight_pct / total_weight_pct * 100). The FILTERED aggregate
-- includes snapshots where the industry is absent → 0; the industry match
-- uses the same LATERAL best-row-per-stock classification join as
-- holdings.sql. Serves GET /api/sec-composition/industry-series.
-- args:
--   code_pred: fragment — "h.code = ANY($1::text[])" (the WHERE predicate)
--   (params stay positional, bound by the service:
--    $1 = codes, $2 = industry_id, $3 = source_type)
    SELECT h.snapshot_date::text AS snapshot_date,
           SUM(h.weight_pct) FILTER (WHERE sc.industry_id = $2) AS weight_pct,
           SUM(h.weight_pct)                                       AS total_weight_pct
      FROM stats.sec_composition h
      LEFT JOIN LATERAL (
        SELECT sc.industry_id
          FROM stats.sec_classification sc
         WHERE sc.code = h.stock_code AND sc.type = 'stock'
         ORDER BY sc.parent_index_weight DESC NULLS LAST, sc.parent_index_code
         LIMIT 1
      ) sc ON true
     WHERE h.source_type = $3
       AND {{code_pred}}
     GROUP BY h.snapshot_date
     ORDER BY h.snapshot_date ASC
