-- The LAST intraday bar (date, time, close) of the code ON one date — the
-- as-of mode's evaluated bar when the intraday table has one that day.
-- args:
--   intraday_table: fragment — the sec_type's intraday table (INTRADAY_TABLES[sec_type])
--   code: value text — the ticker
--   on_date: value date — the as-of day
SELECT date, time, close::float8 AS close FROM {{intraday_table}} WHERE code = {{code}} AND date = {{on_date}} AND close IS NOT NULL ORDER BY time DESC LIMIT 1
