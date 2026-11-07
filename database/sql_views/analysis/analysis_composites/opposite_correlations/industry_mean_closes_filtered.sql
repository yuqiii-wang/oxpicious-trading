-- The composites baseline mean_close load, FILTERED mode: (date,
-- industry_id, pool_size, mean_close) rows for the selected industries
-- only (non-NULL mean) — the MA curves' full-history source.
-- args:
--   baseline_table: fragment — stats.industry_basic_stats
--   codes: value text[] — the sorted selected industry ids
            SELECT extract(epoch from date)::float8 AS date,
                   industry_id, pool_size, mean_close
            FROM {{baseline_table}}
            WHERE mean_close IS NOT NULL
              AND industry_id = ANY({{codes}})
            ORDER BY industry_id, pool_size, date
