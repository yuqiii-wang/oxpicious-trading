-- The hedge_pairs family's data-driven pair universe: per unordered
-- industry pair the FRESHEST offsets reading (analysis_composites.
-- industry_corr_benchmark_offsets, pool 'all', interval 20) whose
-- trailing opposite score clears HP_MIN_PAIR_SCORE.
-- args:
--   offsets_table: fragment — the offsets estimates table
--   pool_size: value int — the offsets pool_size ('all' = 3)
--   interval: value int — the offsets "interval" (20)
--   min_score: value float — the HP_MIN_PAIR_SCORE floor
        SELECT DISTINCT ON (industry_id, benchmark_industry_id)
               industry_id, benchmark_industry_id,
               opposite_score_ma255_255d::float8 AS score,
               offset_sub_corr_ma255_255d::float8 AS rho
        FROM {{offsets_table}}
        WHERE pool_size = {{pool_size}}
          AND "interval" = {{interval}}
          AND opposite_score_ma255_255d > {{min_score}}
        ORDER BY industry_id, benchmark_industry_id, start_date DESC
