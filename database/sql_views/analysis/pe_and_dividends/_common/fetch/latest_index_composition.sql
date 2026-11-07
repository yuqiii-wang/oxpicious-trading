-- pe_and_dividends index stage — the LATEST sec_composition
-- snapshot per index code (source_type='index', weight > 0),
-- held constant across dates (market_beta temporal convention).
-- args:
--   index_codes: value text[] — the active index codes

        WITH latest AS (
            SELECT code, MAX(snapshot_date) AS max_date
            FROM stats.sec_composition
            WHERE source_type = 'index'
              AND stock_code IS NOT NULL
              AND code = ANY({{index_codes}}::text[])
            GROUP BY code
        )
        SELECT
            sc.code AS index_code,
            sc.stock_code,
            sc.weight_pct::float8 AS weight_pct
        FROM stats.sec_composition sc
        JOIN latest ld
            ON sc.code = ld.code AND sc.snapshot_date = ld.max_date
        WHERE sc.source_type = 'index'
          AND sc.stock_code IS NOT NULL
          AND sc.weight_pct > 0
        