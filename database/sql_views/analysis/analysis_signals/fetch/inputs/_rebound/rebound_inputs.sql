-- The rebound (drop-rebound-resolution) family's joined long-format
-- input frame — the bollinger fetch's base shape (price + the day-0
-- ohlcv-boost axes + the turnover-climax amt ratio) plus the code's
-- index PE (stats.index_valuation.pe — the PE-conditioning axis input,
-- NULL where the valuation store has no row); the anchors / trough /
-- peak / resolution layer is computed in-engine.
-- args:
--   codes: value text[] — the sorted requested codes
--   price_expr: fragment — the mov_ave_spread price expression
--   base_table: fragment — the sec_type's base OHLCV source
--   sec_type_code: fragment — the sec_type smallint literal (join keys)
--   pattern_table: fragment — the ohlc_gaps pattern axis store
--   px_vol_table: fragment — the (vol_state, px_speed) axis store
--   amt_select: fragment — the amt_ratio select expression
--   amt_joins: fragment — the amt_ratio join clauses
--   ohl_est_filter: fragment — the OHLC-estimated exclusion predicate
        SELECT b.code,
               extract(epoch from b.date)::float8 AS date,
               {{price_expr}}::float8 AS price,
               v.pe::float8 AS pe,
               g.pattern,
               p.vol_state::float8 AS vol_state,
               p.px_speed::float8 AS px_speed,
               {{amt_select}}
        FROM {{base_table}}
        LEFT JOIN stats.index_valuation v
               ON v.code = b.code AND v.date = b.date
        LEFT JOIN {{pattern_table}} g
               ON g.sec_type = {{sec_type_code}}
              AND g.code = b.code AND g.date = b.date
        LEFT JOIN {{px_vol_table}} p
               ON p.sec_type = {{sec_type_code}}
              AND p.code = b.code AND p.date = b.date
        {{amt_joins}}
        WHERE b.code = ANY({{codes}}::text[])
          AND b.close IS NOT NULL
          {{ohl_est_filter}}
        ORDER BY b.code, b.date ASC
