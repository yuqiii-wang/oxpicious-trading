-- Latest intraday bar (date, time, close) of ONE code — the LIVE breach
-- check's evaluated bar (the sec_type's intraday table probed per code).
-- args:
--   intraday_table: fragment — the sec_type's intraday table (INTRADAY_TABLES[sec_type])
--   code: value text — the ticker to fetch
SELECT date, time, close::float8 AS close FROM {{intraday_table}} WHERE code = {{code}} AND close IS NOT NULL ORDER BY date DESC, time DESC LIMIT 1
