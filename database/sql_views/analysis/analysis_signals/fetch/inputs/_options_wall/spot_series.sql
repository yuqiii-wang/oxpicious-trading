-- The options_wall family's options spot axis (analysis.options_spot_
-- daily close) — the axis the walls were computed against (the breach
-- excesses match the walls' own underlying_close basis, NOT the
-- mov_ave_spread price convention).
-- args:
--   codes: value text[] — the sorted requested underlying codes
        SELECT underlying_code AS code,
               extract(epoch from date)::float8 AS date,
               close::float8 AS price
        FROM analysis.options_spot_daily
        WHERE underlying_code = ANY({{codes}}::text[])
        ORDER BY underlying_code, date ASC
