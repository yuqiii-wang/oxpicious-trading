-- The hedge_pairs trio's broad-market curve closes (stats.index_basic_
-- stats, clean rows only) — one row per date.
-- args:
--   index_stats_table: fragment — stats.index_basic_stats
--   market_code: value text — the market index code (HP_MARKET_CODE)
        SELECT code,
               extract(epoch from date)::float8 AS date,
               close::float8 AS price
        FROM {{index_stats_table}}
        WHERE code = {{market_code}}
          AND close IS NOT NULL
          AND COALESCE(is_ohl_estimated, FALSE) = FALSE
        ORDER BY date ASC
