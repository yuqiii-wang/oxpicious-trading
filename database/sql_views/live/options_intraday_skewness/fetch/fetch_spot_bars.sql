-- The day's 5-min spot bars (time, close) — S(t) of the skew curves.
-- args:
--   spot_table: fragment — the underlying spot table (SPOT_TABLES[target_type])
--   code: value text — the spot code
--   trade_date: value date — the target trading day
        SELECT time, close
        FROM {{spot_table}}
        WHERE code = {{code}} AND date = {{trade_date}} AND close IS NOT NULL
        ORDER BY time
