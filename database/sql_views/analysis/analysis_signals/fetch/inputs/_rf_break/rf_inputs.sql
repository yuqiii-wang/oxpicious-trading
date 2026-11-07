-- The rf_break (roof/floor breakout) family's joined long-format input
-- frame: the DONCHIAN (method 1) and MEDIAN (method 5) estimate rows of
-- the roof/floor estimates table pivoted to one level column per
-- (method, window, side) + the estimates table's own today_close as the
-- price + the code's index PE + the day-0 ohlcv-boost axes.
-- args:
--   codes: value text[] — the sorted requested codes
--   rf_estimate_table: fragment — the roof/floor estimates table
--   sec_type_code: fragment — the sec_type smallint literal
--   method_codes: fragment — the family's method codes ("1,5")
--   level_cols: fragment — the FILTER-aggregated level select list
--   outer_level_cols: fragment — the "d."-prefixed level column list
--   pattern_table: fragment — the ohlc_gaps pattern axis store
--   px_vol_table: fragment — the (vol_state, px_speed) axis store
--   amt_select: fragment — the amt_ratio select expression
--   amt_joins: fragment — the amt_ratio join clauses
        WITH don AS (
            SELECT e.code,
                   e.date,
                   max(e.today_close)::float8 AS price,
                   {{level_cols}}
            FROM {{rf_estimate_table}} e
            WHERE e.sec_type = {{sec_type_code}}
              AND e.method IN ({{method_codes}})
              AND e.code = ANY({{codes}}::text[])
            GROUP BY e.code, e.date
        )
        SELECT d.code,
               extract(epoch from d.date)::float8 AS date,
               d.price,
               v.pe::float8 AS pe,
               {{outer_level_cols}},
               g.pattern,
               p.vol_state::float8 AS vol_state,
               p.px_speed::float8 AS px_speed,
               {{amt_select}}
        FROM don d
        LEFT JOIN stats.index_valuation v
               ON v.code = d.code AND v.date = d.date
        LEFT JOIN {{pattern_table}} g
               ON g.sec_type = {{sec_type_code}}
              AND g.code = d.code AND g.date = d.date
        LEFT JOIN {{px_vol_table}} p
               ON p.sec_type = {{sec_type_code}}
              AND p.code = d.code AND p.date = d.date
        {{amt_joins}}
        ORDER BY d.code, d.date ASC
