-- serves: GET /api/analysis/signals-audit/summary?period=<year>
-- The per-family census rows for ONE calendar-year period (the audit
-- page's family overview table; the overlap heatmap's axis universe).
-- args:
--   sec_type: value smallint — the audit's security scope
--   period: value smallint — the calendar year
--   signal_type_decode: fragment — labelDecodeSql("signal_type", "t.signal_type")
SELECT t.period,
       t.period_start,
       t.period_end,
       t.signal_type,
       {{signal_type_decode}}      AS signal_type_label,
       t.n_events,
       t.n_buy,
       t.n_sell,
       t.n_codes,
       t.n_days,
       t.signal_universe,
       t.first_date,
       t.last_date
FROM analysis_signals.signal_type_audit t
WHERE t.sec_type = {{sec_type}}::smallint
  AND t.period = {{period}}::smallint
ORDER BY t.n_events DESC, t.signal_type
