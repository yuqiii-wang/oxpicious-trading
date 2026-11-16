-- Market crowdedness — the INDEX-side pool membership: the latest
-- stats.sec_composition snapshot (source_type='index', weight_pct > 0)
-- per active classified index (stats.sec_classification type='index',
-- is_active). Full-universe scope (2026-10-31 authorization).
-- args: (none)
    WITH latest AS (
        SELECT code, MAX(snapshot_date) AS max_date
        FROM stats.sec_composition
        WHERE source_type = 'index'
          AND stock_code IS NOT NULL
        GROUP BY code
    )
    SELECT sc.code AS pool_code, sc.stock_code
    FROM stats.sec_composition sc
    JOIN latest ld
        ON sc.code = ld.code AND sc.snapshot_date = ld.max_date
    WHERE sc.source_type = 'index'
      AND sc.stock_code IS NOT NULL
      AND sc.weight_pct > 0
      AND sc.code IN (
          SELECT cls.code FROM stats.sec_classification cls
          WHERE cls.type = 'index' AND cls.is_active = TRUE
      )
