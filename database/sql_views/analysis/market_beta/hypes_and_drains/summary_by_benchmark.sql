-- Hypes & drains step sanity summary — row counts + date range per
-- (benchmark_code, period_days, weighting) from the stored per-date rows
-- of analysis.industry_hypes_and_drains (log output only, first 60
-- groups).
-- args: none
        SELECT benchmark_code, period_days, weighting,
               COUNT(*) AS n_rows,
               COUNT(DISTINCT industry_id) AS n_industries,
               MIN(date) AS first_date,
               MAX(date) AS last_date,
               ROUND(AVG(metric_value), 6) AS avg_metric
        FROM analysis.industry_hypes_and_drains
        GROUP BY benchmark_code, period_days, weighting
        ORDER BY benchmark_code, period_days, weighting
        LIMIT 60
