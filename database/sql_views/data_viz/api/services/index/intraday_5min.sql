-- 5-minute intraday bars for one (code, date) from
-- stats.index_intraday_5min, ordered by time ascending.
-- Serves GET /api/index/intraday-5min.
-- args:
--   code: value text — the index code
--   date: value text — trading day (YYYY-MM-DD)
SELECT to_char(time, 'HH24:MI:SS') AS time,
            open, high, low, close, change, change_pct
       FROM stats.index_intraday_5min
      WHERE code = {{code}} AND date = {{date}}::date
      ORDER BY time ASC
