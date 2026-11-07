-- The options_vix family's options spot axis (analysis.options_spot_
-- daily close) over the requested option underlyings — the corr's spot
-- leg and the forward changes' price base.
-- args:
--   codes: value text[] — the sorted requested underlying codes
        SELECT underlying_code AS code,
               extract(epoch from date)::float8 AS date,
               close::float8 AS price
        FROM analysis.options_spot_daily
        WHERE underlying_code = ANY({{codes}}::text[])
        ORDER BY underlying_code, date ASC
