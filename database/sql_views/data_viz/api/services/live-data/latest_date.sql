-- Latest intraday date across ALL codes of the requested type — the
-- combined endpoint's date resolution when the request omits the date.
-- args:
--   intraday_table: fragment — TABLES[sec_type].table
--     (stats.index_intraday_5min / stats.stock_intraday_5min)
SELECT MAX(date)::text AS date FROM {{intraday_table}}
