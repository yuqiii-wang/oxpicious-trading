-- The run's codes' market-regime DAY label codes ON the anchor date
-- (stats.market_regimes point lookups — the daily labels are static
-- historical rows built from shift-1 trailing inputs; the forecast
-- approximates tomorrow's regime by the anchor's own label, the live
-- tier's fetch_regime_state convention: days with no row read calm 0,
-- filled by the caller).
-- args:
--   sec_type: value smallint — the sec_type's SEC_TYPE_CODE
--   codes: value text[] — the run's codes
--   on_date: value date — the day to look up
SELECT r.code, r.regime
FROM stats.market_regimes r
WHERE r.sec_type = {{sec_type}}
  AND r.code = ANY({{codes}})
  AND r.date = {{on_date}}
