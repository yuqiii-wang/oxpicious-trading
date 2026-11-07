-- The mov_rsi (RSI percentile) family's joined long-format input frame:
-- one row per (code, date) with the price (the mov_ave_spread
-- convention), one rsi_{W}days column per RSI window, the day-level
-- tail-episode flags + episode sizes (analysis.trend_episode_days) and
-- the day-0 ohlcv-boost axis columns + the turnover-climax amt ratio.
-- args:
--   codes: value text[] — the sorted requested codes ($1)
--   since: value date — the lower date bound ($2)
--   r_sec_type: fragment — the r CTE's "$3" positional reference. The
--     original text binds codes as $1 / since as $2 / the sec_type code
--     as $3 while the CTE appears FIRST in the text, and the loader
--     numbers slots by first use — so the fixed reference rides verbatim
--     and the caller binds the sec_type smallint code as its third
--     positional arg.
--   rsi_table: fragment — analysis.mov_ave_rsi (the value source)
--   price_expr: fragment — the mov_ave_spread price expression
--   rsi_cols: fragment — the rsi_{W}days::float8 select list
--   flag_cols: fragment — the is_typical_{W}days select list
--   size_cols: fragment — the ep_size_{W}days select list
--   amt_select: fragment — the amt_ratio select expression
--   base_table: fragment — the sec_type's base OHLCV source
--   sec_type_code: fragment — the sec_type smallint literal (join keys)
--   pattern_table: fragment — the ohlc_gaps pattern axis store
--   px_vol_table: fragment — the (vol_state, px_speed) axis store
--   amt_joins: fragment — the amt_ratio join clauses
--   ohl_est_filter: fragment — the OHLC-estimated exclusion predicate
        WITH r AS (
            SELECT * FROM {{rsi_table}}
            WHERE sec_type = {{r_sec_type}}
        )
        SELECT b.code,
               extract(epoch from b.date)::float8 AS date,
               {{price_expr}}::float8 AS price,
               {{rsi_cols}},
               {{flag_cols}},
               {{size_cols}},
               g.pattern,
               p.vol_state::float8 AS vol_state,
               p.px_speed::float8 AS px_speed,
               {{amt_select}}
        FROM {{base_table}}
        LEFT JOIN {{rsi_table}} r
               ON r.sec_type = {{sec_type_code}}
              AND r.code = b.code AND r.date = b.date
        LEFT JOIN analysis.trend_episode_days ted
               ON ted.sec_type = {{sec_type_code}}
              AND ted.code = b.code AND ted.date = b.date
        LEFT JOIN {{pattern_table}} g
               ON g.sec_type = {{sec_type_code}}
              AND g.code = b.code AND g.date = b.date
        LEFT JOIN {{px_vol_table}} p
               ON p.sec_type = {{sec_type_code}}
              AND p.code = b.code AND p.date = b.date
        {{amt_joins}}
        WHERE b.code = ANY({{codes}}::text[])
          AND b.close IS NOT NULL
          AND b.date >= {{since}}
          {{ohl_est_filter}}
        ORDER BY b.code, b.date ASC
