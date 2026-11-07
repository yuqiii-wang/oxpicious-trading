-- Hypes & drains SEASONAL sanity summary — season counts + date span per
-- (benchmark_code, period_days) from analysis.industry_hypes_seasonal
-- (log output only, first 30 groups).
-- args: none
        SELECT
            benchmark_code,
            period_days,
            COUNT(DISTINCT season_qkey)  AS n_seasons,
            COUNT(*)                     AS n_rows,
            COUNT(DISTINCT industry_id)  AS n_industries,
            MIN(season_qkey)             AS first_season,
            MAX(season_qkey)             AS last_season
        FROM analysis.industry_hypes_seasonal
        GROUP BY benchmark_code, period_days
        ORDER BY benchmark_code, period_days
        LIMIT 30
