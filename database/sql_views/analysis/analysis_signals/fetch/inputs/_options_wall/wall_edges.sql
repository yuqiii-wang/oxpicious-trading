-- The options_wall family's CHAIN-DOMINANT zone wall edges (analysis.
-- options_walls — dominant per (code, date, side) by strength_score then
-- wall_oi): the put side's OUTER edge + CENTER and the call side's OUTER
-- cap; the frame pivots the side rows into edge columns.
-- args:
--   codes: value text[] — the sorted requested underlying codes
        SELECT DISTINCT ON (underlying_code, date, option_type)
               underlying_code AS code,
               extract(epoch from date)::float8 AS date,
               option_type,
               wall_low::float8 AS wall_low,
               wall_high::float8 AS wall_high,
               wall_center::float8 AS wall_center
        FROM analysis.options_walls
        WHERE wall_type = 'zone'
          AND underlying_code = ANY({{codes}}::text[])
        ORDER BY underlying_code, date, option_type,
                 strength_score DESC NULLS LAST,
                 wall_oi DESC NULLS LAST
