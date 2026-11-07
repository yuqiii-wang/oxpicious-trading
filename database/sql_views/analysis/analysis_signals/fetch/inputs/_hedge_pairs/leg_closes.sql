-- The hedge_pairs legs' composite closes (stats.industry_basic_stats
-- mean_close, pool 'all') — one row per (industry, date).
-- args:
--   industry_stats_table: fragment — stats.industry_basic_stats
--   pool_size: value int — the offsets pool_size ('all' = 3)
--   codes: value text[] — the sorted pair-leg industry ids
        SELECT industry_id AS code,
               extract(epoch from date)::float8 AS date,
               mean_close::float8 AS price
        FROM {{industry_stats_table}}
        WHERE pool_size = {{pool_size}}
          AND industry_id = ANY({{codes}})
          AND mean_close IS NOT NULL
        ORDER BY industry_id, date ASC
