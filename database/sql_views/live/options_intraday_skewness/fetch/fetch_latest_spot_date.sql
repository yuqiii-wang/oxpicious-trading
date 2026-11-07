-- LIVE-mode date resolution: the spot table's latest date for the code.
-- args:
--   spot_table: fragment — the underlying spot table (SPOT_TABLES[target_type])
--   code: value text — the spot code
SELECT MAX(date) FROM {{spot_table}} WHERE code = {{code}}
