-- margins — universe filter: codes with rz_balance at/above the
-- sec_type minimum within the recent window (stale / delisted /
-- sub-threshold securities excluded from the analysis universe).
-- args:
--   src_table: fragment — the sec_type's margin source table
--       (stats.etf_liquidity_margin / stats.stock_liquidity_margin)
--   cutoff: value date — window start (ref_date - UNIVERSE_RECENT_DAYS)
--   ref_date: value date — reference today
--   min_balance: value numeric — the sec_type's rz_balance floor

        SELECT DISTINCT code
        FROM {{src_table}}
        WHERE date >= {{cutoff}}::date
          AND date <= {{ref_date}}::date
          AND rz_balance >= {{min_balance}}::numeric
        