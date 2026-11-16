-- The crowdedness family's joined long-format input frame — the base
-- OHLCV frame with BOTH crowdedness hot-set bases LEFT JOINed per
-- (code, sec_type, date): the DAILY-ROLLING hot set
-- (analysis.market_crowdedness — crowdedness_score the RAW
-- 0.30/0.30/0.25/0.15 blend of the four pillar percentiles,
-- crowdedness_score_ma5 the pipeline's ONLINE read) and the THIS-YEAR
-- pinned hot set (analysis.market_crowdedness_year joined on
-- hot_year = the date's own calendar year — each year's frozen roster
-- chained across years; _year-suffixed columns), the hot-set TECH
-- profile (analysis.market_crowdedness_hot_tech — the median
-- px-vs-MA20/MA60 percentiles + the amount-vs-MA20 percentile: the
-- tech_washout / tech_euphoria triggers) + the day-0 ohlcv-boost axis
-- columns + the turnover-climax amt ratio.
-- args:
--   codes: value text[] — the sorted requested codes
--   price_expr: fragment — the mov_ave_spread price expression
--   base_table: fragment — the sec_type's base OHLCV source (aliased b)
--   crowd_table: fragment — analysis.market_crowdedness
--   crowd_year_table: fragment — analysis.market_crowdedness_year
--   hot_tech_table: fragment — analysis.market_crowdedness_hot_tech
--   sec_type_code: fragment — the sec_type smallint literal (join keys)
--   pattern_table: fragment — the ohlc_gaps pattern axis store
--   px_vol_table: fragment — the (vol_state, px_speed) axis store
--   amt_select: fragment — the amt_ratio select expression
--   amt_joins: fragment — the amt_ratio join clauses
--   ohl_est_filter: fragment — the OHLC-estimated exclusion predicate
        SELECT b.code,
               extract(epoch from b.date)::float8 AS date,
               {{price_expr}}::float8 AS price,
               c.crowdedness_score::float8,
               c.crowdedness_score_ma5::float8,
               y.crowdedness_score::float8 AS crowdedness_score_year,
               y.crowdedness_score_ma5::float8
                   AS crowdedness_score_ma5_year,
               h.median_px_vs_ma20_pct::float8,
               h.median_px_vs_ma60_pct::float8,
               h.median_amt_vs_ma20_pct::float8,
               g.pattern,
               p.vol_state::float8 AS vol_state,
               p.px_speed::float8 AS px_speed,
               {{amt_select}}
        FROM {{base_table}}
        LEFT JOIN {{crowd_table}} c
               ON c.sec_type = {{sec_type_code}}
              AND c.code = b.code AND c.date = b.date
        LEFT JOIN {{crowd_year_table}} y
               ON y.sec_type = {{sec_type_code}}
              AND y.code = b.code AND y.date = b.date
              AND y.hot_year = extract(year from b.date)
        LEFT JOIN {{hot_tech_table}} h
               ON h.sec_type = {{sec_type_code}}
              AND h.code = b.code AND h.date = b.date
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
