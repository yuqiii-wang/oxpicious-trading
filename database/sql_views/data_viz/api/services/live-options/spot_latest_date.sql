-- Latest 5-min spot-bar date for an options underlying — the date
-- resolution when no explicit date param is given.
-- args:
--   codes: value text[] — codeVariants(underlying) (suffixed ETF + bare forms)
--   spot_table: fragment — resolveSpotTable(target_type)
SELECT MAX(date) AS date FROM {{spot_table}} WHERE code = ANY({{codes}}::text[])
