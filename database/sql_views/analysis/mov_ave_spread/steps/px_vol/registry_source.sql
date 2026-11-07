-- price_vs_amt registry source fetch — price + trading_amount per
-- (code, date) for ONE sec_type with the shared per-sec_type
-- conventions (ETF price = COALESCE(adj_close, close); close-less rows
-- excluded; estimated-OHLC rows excluded). The base table / price and
-- trading-amount expressions come from the mov_ave_spread config
-- (PRICE_SOURCE / AMT_SOURCE) and ride in as fragment args.
-- Feeds analysis.mov_ave_spread.steps.px_vol.fetch_price_vs_amt_source.
-- args:
--   base: fragment — the sec_type's base-table FROM expression
--         (PRICE_SOURCE[sec_type] table part, incl. its joins/alias)
--   price_expr: fragment — the price column expression
--         (PRICE_SOURCE[sec_type] price part, e.g. "b.close")
--   amt_join: fragment — the liquidity-margin LEFT JOIN
--         (AMT_SOURCE[sec_type] join part; empty for index/industry)
--   amt_expr: fragment — the trading_amount expression
--         (AMT_SOURCE[sec_type] amount part)
--   est_filter: fragment — the is_ohl_estimated exclusion predicate
--         (AMT_SOURCE[sec_type] filter part)
--   codes: value text[] — the codes to fetch
        SELECT b.code,
               extract(epoch from b.date)::float8 AS date,
               {{price_expr}}::float8 AS price,
               {{amt_expr}}::float8 AS trading_amount
        FROM {{base}}
        {{amt_join}}
        WHERE b.code = ANY({{codes}}::text[])
          AND b.close IS NOT NULL
          {{est_filter}}
        ORDER BY b.code, b.date ASC
