-- Correlations step (analysis.market_beta internal pipeline) — load the
-- FULL per-(industry_id, pool_size) mean_close history from
-- stats.industry_basic_stats (built by builds.industry) for the windowed
-- MA-corr compute. Only non-NULL mean_close rows are useful — NULL rows
-- mean no member indices contributed to that (date, industry, pool)
-- slice. Dates arrive as float8 epoch (extract(epoch)) per the DB-read
-- convention so the frame materializes datetime64 in one host pass.
-- args: none
            SELECT extract(epoch from date)::float8 AS date,
                   industry_id, pool_size, mean_close
            FROM stats.industry_basic_stats
            WHERE mean_close IS NOT NULL
            ORDER BY industry_id, pool_size, date
