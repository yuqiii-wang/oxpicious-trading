-- Distinct 5-min spot-bar dates for an options underlying, descending —
-- the Live Data Options tab's date picker. The spot table routes by the
-- options tables' underlying_target_type (stats.etf_intraday_5min for ETF
-- targets / stats.index_intraday_5min for INDEX targets).
-- args:
--   codes: value text[] — codeVariants(underlying) (suffixed ETF + bare forms)
--   spot_table: fragment — resolveSpotTable(target_type)

SELECT DISTINCT date
       FROM {{spot_table}}
      WHERE code = ANY({{codes}}::text[])
      ORDER BY date DESC
