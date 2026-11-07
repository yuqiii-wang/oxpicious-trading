-- Market-beta chart by code — per-index close series + latest stock_num for
-- ONE index (strategy-primary indices without an industry_id). Serves the
-- by-code variant of GET /api/analysis/market-beta chart.
-- args:
--   code: value text — the index code (bare, exchange suffix stripped)
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
      AND sc.code = {{code}}::text
    ORDER BY ib.date ASC
