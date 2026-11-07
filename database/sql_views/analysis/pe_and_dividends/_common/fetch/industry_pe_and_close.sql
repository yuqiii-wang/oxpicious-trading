-- pe_and_dividends industry stage — per-(industry_id, date)
-- composite close + mean_pe from industry_basic_stats pool-'all'
-- (pool_size = 3) slice, epoch float8 dates, mean_close non-null.
-- args:
--   codes: value text[] — the active industry ids

        SELECT
            b.industry_id AS code,
            extract(epoch from b.date)::float8 AS date,
            b.mean_close::float8 AS close,
            b.mean_pe::float8 AS pe
        FROM stats.industry_basic_stats b
        WHERE b.industry_id = ANY({{codes}}::text[])
          AND b.pool_size = 3
          AND b.mean_close IS NOT NULL
        ORDER BY b.industry_id, b.date ASC
        