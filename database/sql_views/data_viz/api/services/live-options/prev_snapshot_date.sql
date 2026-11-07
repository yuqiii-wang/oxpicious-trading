-- The prev-trading-day options quote snapshot date the OI base came from
-- (latest stats.v_options_quote date strictly before the chart date).
-- args:
--   date: value text — trading day (YYYY-MM-DD)
SELECT MAX(date) AS date FROM stats.v_options_quote WHERE date < {{date}}::date
