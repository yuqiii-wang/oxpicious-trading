-- Market-beta chart — per-index close series + latest stock_num for ONE
-- industry, composition-only filter (indices without any composition
-- snapshot are excluded entirely). Serves GET /api/analysis/market-beta chart.
-- args:
--   industry_id: value text — the industry to list member indices for
    WITH stock_counts AS (
        SELECT code, snapshot_date,
               COUNT(DISTINCT stock_code) AS stock_num
        FROM stats.sec_composition
        WHERE source_type = 'index'
        GROUP BY code, snapshot_date
    )
    SELECT
      sc.code,
      COALESCE(sc.name, '') AS name,
      sc.exchange,
      ib.date,
      ib.close,
      latest.stock_num
    FROM stats.sec_classification sc
    JOIN stats.index_basic_stats ib
      ON ib.code = sc.code
    LEFT JOIN LATERAL (
        SELECT stock_num
        FROM stock_counts sc2
        WHERE sc2.code = ib.code
        ORDER BY snapshot_date DESC
        LIMIT 1
    ) latest ON true
    WHERE sc.type = 'index'
      AND sc.is_active = TRUE
      AND sc.industry_id = {{industry_id}}::text
      AND EXISTS (
          SELECT 1 FROM stats.sec_composition sc3
          WHERE sc3.code = sc.code AND sc3.source_type = 'index'
      )
    ORDER BY sc.code, ib.date ASC
