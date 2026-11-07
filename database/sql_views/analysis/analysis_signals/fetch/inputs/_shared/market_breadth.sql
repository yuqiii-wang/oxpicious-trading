-- The sec_type's MARKET-LEVEL breadth raw counts: adv/dec per date over
-- EVERY clean-close code of the sec_type's base stats table (the whole
-- universe — breadth is a market property). The engines derive the
-- breadth EMA / thrust / 9to1 states from these per-date counts.
-- args:
--   base_table: fragment — the sec_type's base OHLCV table
--   ohl_filter: fragment — the clean-row filter the family fetches
--     admit ("AND COALESCE(b.is_ohl_estimated, FALSE) = FALSE")
        WITH lagged AS (
            SELECT b.date,
                   b.close,
                   lag(b.close) OVER (
                       PARTITION BY b.code ORDER BY b.date
                   ) AS prev_close
            FROM {{base_table}} b
            WHERE b.close IS NOT NULL
              {{ohl_filter}}
        )
        SELECT extract(epoch from date)::float8 AS date,
               count(*) FILTER (WHERE close > prev_close) AS adv,
               count(*) FILTER (WHERE close < prev_close) AS dec
        FROM lagged
        WHERE prev_close IS NOT NULL
        GROUP BY date
        ORDER BY date
