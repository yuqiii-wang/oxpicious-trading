-- The options_vix family's vol-index series — the 30d model-free vol
-- index (analysis.options_vol_index) over the requested option
-- underlyings; the family's VALUE SPACE and its limiting input.
-- args:
--   codes: value text[] — the sorted requested underlying codes
        SELECT underlying_code AS code,
               extract(epoch from date)::float8 AS date,
               vol_index_30d::float8 AS vol30
        FROM analysis.options_vol_index
        WHERE underlying_code = ANY({{codes}}::text[])
        ORDER BY underlying_code, date ASC
