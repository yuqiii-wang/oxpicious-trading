-- mov_ave_spread source fetch (stock) — per-(code, date) raw OHLC +
-- pre-computed MA/EMA + trading_amount rows for a caller-supplied code
-- universe: stock_identity x stock_basic_stats x stock_tech_stats x
-- stock_liquidity_margin (no adjustment table — stocks use raw close
-- as price).
-- Feeds analysis.mov_ave_spread.fetch_source_data (the pipeline's
-- source frame).
-- args:
--   codes: value text[] — the active code universe to fetch (bound
--          most-important-first by order_codes_or_sorted_async)
            SELECT
                i.code,
                extract(epoch from i.date)::float8 AS date,
                b.close::float8 AS price,
                b.open::float8  AS open,
                b.low::float8   AS low,
                b.high::float8  AS high,
                t.ma5::float8, t.ma20::float8, t.ma60::float8,
                t.ma120::float8, t.ma255::float8,
                t.ema6::float8, t.ema12::float8, t.ema26::float8,
                m.trading_amount::float8,
                b.is_ohl_estimated
            FROM stats.stock_identity i
            JOIN stats.stock_basic_stats b ON b.date = i.date AND b.code = i.code
            JOIN stats.stock_tech_stats  t ON t.date = i.date AND t.code = i.code
            JOIN stats.stock_liquidity_margin m ON m.date = i.date AND m.code = i.code
            WHERE i.code = ANY({{codes}}::text[])
              AND b.close IS NOT NULL
            ORDER BY i.code, i.date ASC
