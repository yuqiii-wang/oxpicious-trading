-- Available dates — the union of intraday dates for the requested security
-- type, descending (most recent first). Serves GET /api/live-data/dates.
-- args:
--   intraday_table: fragment — TABLES[sec_type].table
--     (stats.index_intraday_5min / stats.stock_intraday_5min)
SELECT DISTINCT date::text AS date
       FROM {{intraday_table}}
      ORDER BY date DESC
