-- Map the options tables' bare underlying code onto the spot table's code
-- format (ETF spot rows and index spot rows are BOTH bare 6-digit codes —
-- a direct equality match); None when the spot table has no row that day.
-- args:
--   spot_table: fragment — the underlying spot table (SPOT_TABLES[target_type])
--   trade_date: value date — the target trading day
--   code: value text — the underlying code to match
        SELECT code FROM {{spot_table}}
        WHERE date = {{trade_date}}
          AND code = {{code}}
        ORDER BY code
        LIMIT 1
