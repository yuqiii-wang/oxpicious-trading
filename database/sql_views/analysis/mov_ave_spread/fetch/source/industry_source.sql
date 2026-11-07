-- mov_ave_spread source fetch (industry) — per-(industry_id, date)
-- rebased-to-100 composite OHLC + union-stock turnover rows for a
-- caller-supplied industry universe: industry_identity x
-- industry_basic_stats' pool-'all' (pool_size = 3) slice. No tech_stats
-- table — the MA/EMA columns are derived in-pipeline after the load.
-- Feeds analysis.mov_ave_spread.fetch_source_data (the pipeline's
-- source frame).
-- args:
--   codes: value text[] — the active industry codes to fetch (bound
--          most-important-first by order_codes_or_sorted_async)
            SELECT
                i.code,
                extract(epoch from i.date)::float8 AS date,
                b.mean_close::float8 AS price,
                b.mean_open::float8  AS open,
                b.mean_low::float8   AS low,
                b.mean_high::float8  AS high,
                b.total_trading_amount::float8,
                FALSE AS is_ohl_estimated
            FROM stats.industry_identity i
            JOIN stats.industry_basic_stats b
              ON b.date = i.date AND b.industry_id = i.code
            WHERE i.code = ANY({{codes}}::text[])
              AND b.pool_size = 3
              AND b.mean_close IS NOT NULL
            ORDER BY i.code, i.date ASC
