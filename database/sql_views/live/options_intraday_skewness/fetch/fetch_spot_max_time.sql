-- Latest 5-min bar time for the code-day — the staleness yardstick the
-- incremental skip compares the stored series against.
-- args:
--   spot_table: fragment — the underlying spot table (SPOT_TABLES[target_type])
--   code: value text — the spot code
--   trade_date: value date — the target trading day
SELECT MAX(time) FROM {{spot_table}} WHERE code = {{code}} AND date = {{trade_date}}
