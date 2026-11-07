-- The composites baseline mean_close load (full universe): (date,
-- industry_id, pool_size, mean_close) rows, non-NULL mean — the MA
-- curves' full-history source; the optional date bound prunes the
-- bounded incremental load to the tail span the targeted windows need.
-- args:
--   baseline_table: fragment — stats.industry_basic_stats
--   date_bound: fragment — the optional incremental load bound
--     ("AND date >= $1" bound to load_from, "" on a full-history load)
            SELECT extract(epoch from date)::float8 AS date,
                   industry_id, pool_size, mean_close
            FROM {{baseline_table}}
            WHERE mean_close IS NOT NULL
              {{date_bound}}
            ORDER BY industry_id, pool_size, date
