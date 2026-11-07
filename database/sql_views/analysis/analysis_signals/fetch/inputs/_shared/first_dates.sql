-- Per-code TRUE first data date (min(date) on the base OHLCV table with
-- the same close IS NOT NULL filter the input fetch admits) — the live
-- gate's input (a code joins a snapshot from its own first-data date).
-- args:
--   base_table: fragment — the sec_type's base OHLCV table
--     (stats.{index,etf,stock}_basic_stats)
--   codes: value text[] — the sorted requested codes
        SELECT b.code, min(b.date) AS first_date
        FROM {{base_table}} b
        WHERE b.code = ANY({{codes}}::text[])
          AND b.close IS NOT NULL
        GROUP BY b.code
