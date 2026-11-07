-- The options_wall family's 30d implied vol index series (analysis.
-- options_vol_index) over the requested option underlyings — the VRP
-- gate's implied leg.
-- args:
--   codes: value text[] — the sorted requested underlying codes
        SELECT underlying_code AS code,
               extract(epoch from date)::float8 AS date,
               vol_index_30d::float8 AS vol30
        FROM analysis.options_vol_index
        WHERE underlying_code = ANY({{codes}}::text[])
        ORDER BY underlying_code, date ASC
