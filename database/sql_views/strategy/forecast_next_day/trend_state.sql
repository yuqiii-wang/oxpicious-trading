-- The run's codes' TREND codes at the latest signal-rules-tier
-- trend_days row at-or-before the date — the forecast's day-context
-- input (tomorrow's trend approximated by the anchor's own latest
-- classification, a documented approximation; the live tier's
-- fetch_trend_state contract widened to the run's codes at once).
-- args:
--   trend_days_table: fragment — the trend-days store
--   sec_type: value smallint — the sec_type's SEC_TYPE_CODE
--   codes: value text[] — the run's codes
--   on_date: value date — bound the lookup at-or-before this day
SELECT DISTINCT ON (t.code) t.code, t.trend
FROM {{trend_days_table}} t
WHERE t.sec_type = {{sec_type}}
  AND t.code = ANY({{codes}})
  AND t.date <= {{on_date}}
ORDER BY t.code, t.date DESC
