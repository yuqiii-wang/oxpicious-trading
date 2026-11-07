-- Prev-trading-day options snapshot date: the latest stats.v_options_quote
-- date STRICTLY BEFORE the target date (during a live session the target
-- day's own quote rows do not exist yet; for older dates the prev-day
-- snapshot is exactly the position base the estimator adds traded volume
-- onto).
-- args:
--   trade_date: value date — the target trading day
SELECT MAX(date) FROM stats.v_options_quote WHERE date < {{trade_date}}::date
