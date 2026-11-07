-- The intraday OI-weighted moneyness skewness series for one (underlying,
-- date) from live.options_intraday_skewness — written by
-- python -m live.options_intraday_skewness (the MEAN row hides under the
-- sentinel expiry 9998-12-31). Serves the Live Data Options tab.
-- args:
--   underlying_code: value text — the options underlying code
--   date: value text — trading day (YYYY-MM-DD)
SELECT to_char(time, 'HH24:MI') AS time,
              to_char(expiry_date, 'YYYY-MM-DD') AS expiry_date,
              spot::float8 AS spot,
              skew_price::float8 AS skew_price,
              skew_pct::float8 AS skew_pct,
              oi_total::float8 AS oi_total,
              otm_call_share::float8 AS otm_call_share,
              otm_put_share::float8 AS otm_put_share,
              skew_type
         FROM live.options_intraday_skewness
        WHERE underlying_code = {{underlying_code}} AND date = {{date}}::date
        ORDER BY time, expiry_date
