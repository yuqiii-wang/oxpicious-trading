-- Market crowdedness — the INDUSTRY-side pool membership: the DISTINCT
-- union of member stocks across the industry's member indices (the
-- builds.cross_stats latest/holdings/industry_stocks CTE chain, the
-- single source of truth for industry-union membership; strategy themes
-- excluded via is_industry_not_strategy).
-- args: (none)
    WITH latest AS (
        SELECT code, MAX(snapshot_date) AS max_date
        FROM stats.sec_composition
        WHERE source_type = 'index'
          AND stock_code IS NOT NULL
        GROUP BY code
    ),
    holdings AS (
        SELECT sc.code, sc.stock_code
        FROM stats.sec_composition sc
        JOIN latest ld
            ON sc.code = ld.code AND sc.snapshot_date = ld.max_date
        WHERE sc.source_type = 'index'
          AND sc.stock_code IS NOT NULL
    ),
    industry_stocks AS (
        SELECT DISTINCT cls.industry_id, h.stock_code
        FROM holdings h
        JOIN stats.sec_classification cls
            ON cls.code = h.code AND cls.type = 'index'
        WHERE cls.industry_id IS NOT NULL
          AND cls.industry_id <> ''
          AND cls.is_active = TRUE
          AND cls.is_industry_not_strategy = TRUE
    )
    SELECT industry_id AS pool_code, stock_code FROM industry_stocks
