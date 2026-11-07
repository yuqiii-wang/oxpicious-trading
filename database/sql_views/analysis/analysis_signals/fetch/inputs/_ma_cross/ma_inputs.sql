-- The ma_cross family's joined long-format input frame, keyed on the
-- family's value source (analysis.mov_ave_spreads_detail — the table's
-- own price_vs_ma{20,60,120,255} gaps + price_slope) with the price LEFT
-- JOINed from the base OHLCV table + the day-0 ohlcv-boost axis columns
-- + the turnover-climax amt ratio.
-- args:
--   codes: value text[] — the sorted requested codes
--   ma_gap_table: fragment — analysis.mov_ave_spreads_detail
--   price_expr: fragment — the mov_ave_spread price expression
--   gap_cols: fragment — the price_vs_ma{W}::float8 gap select list
--   base_table: fragment — the sec_type's base OHLCV source (alias b)
--   sec_type_code: fragment — the sec_type smallint literal (join + filter)
--   pattern_table: fragment — the ohlc_gaps pattern axis store
--   px_vol_table: fragment — the (vol_state, px_speed) axis store
--   amt_select: fragment — the amt_ratio select expression
--   amt_joins: fragment — the amt_ratio join clauses (the detail-table
--     alias d already carries trading_amt_ma255 — no extra ma join)
        SELECT d.code,
               extract(epoch from d.date)::float8 AS date,
               {{price_expr}}::float8 AS price,
               d.price_slope::float8 AS price_slope,
               {{gap_cols}},
               g.pattern,
               p.vol_state::float8 AS vol_state,
               p.px_speed::float8 AS px_speed,
               {{amt_select}}
        FROM {{ma_gap_table}} d
        LEFT JOIN {{base_table}}
               ON b.code = d.code AND b.date = d.date
        LEFT JOIN {{pattern_table}} g
               ON g.sec_type = {{sec_type_code}}
              AND g.code = d.code AND g.date = d.date
        LEFT JOIN {{px_vol_table}} p
               ON p.sec_type = {{sec_type_code}}
              AND p.code = d.code AND p.date = d.date
        {{amt_joins}}
        WHERE d.sec_type = {{sec_type_code}}
          AND d.code = ANY({{codes}}::text[])
          AND {{price_expr}} IS NOT NULL
        ORDER BY d.code, d.date ASC
