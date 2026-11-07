-- ONE (sec_type, code)'s TREND spans: contiguous same-trend runs over
-- analysis.trend_days (gaps-and-islands in SQL — the store is small and
-- always fresh), default window the last 3 years. The shared
-- CodeTrendChart's Trend toggle shades the spans per trend; trend is
-- decoded from the label code back to the label the UI speaks.
-- Serves GET /api/live-data/trading-signals/trend-spans.
-- args:
--   sec_type_code: value int — LABEL_CODE.sec_type[secType]
--   code: value text — the code whose trend spans to list
--   window_days: value int — the clamped look-back window (90..3650)
--   trend_decode: fragment — labelDecodeSql("trend", "trend")
WITH d AS (
       SELECT date, trend,
              -- group-change flag (gaps-and-islands): true when the trend
              -- differs from the previous row (the first row's NULL lag is
              -- DISTINCT → 1 — trend itself is NOT NULL)
              (trend IS DISTINCT FROM lag(trend) OVER (ORDER BY date))::int AS ch
       FROM analysis.trend_days
       WHERE sec_type = {{sec_type_code}} AND code = {{code}}
         AND date >= CURRENT_DATE - make_interval(days => {{window_days}})
     ), g AS (
       SELECT trend, date,
              sum(ch) OVER (ORDER BY date) AS grp
       FROM d
     )
     SELECT {{trend_decode}} AS trend,
            to_char(min(date), 'YYYY-MM-DD') AS start_date,
            to_char(max(date), 'YYYY-MM-DD') AS end_date,
            count(*)::int AS span_days
     FROM g
     GROUP BY trend, grp
     ORDER BY trend, start_date
