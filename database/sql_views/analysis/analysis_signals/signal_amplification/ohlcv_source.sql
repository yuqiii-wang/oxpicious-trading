-- The signal-amplification tier's raw OHLCV source frame: one row per
-- (code, date) from the sec_type's base stats table — raw open/high/low/
-- close (ETFs adjusted, the mov_ave_spread PRICE_SOURCE convention) +
-- trading_amount + the estimated-OHLC exclusion. The estimated rows are
-- excluded HERE (the ohlc_gaps convention: synthetic OHLC would zero the
-- wick/range gaps; the exclusion is also the prev-row break — prev_*
-- references never cross a synthetic row).
-- serves: analysis.analysis_signals.signal_amplification (ohlcv_ext build)
-- args:
--   codes: value text[] — the sorted requested codes ($1)
--   since: value date — the lower date bound ($2)
--   base_from: fragment — the FROM clause (base table + the ETF adjustment join)
--   open_expr: fragment — the adjusted-open expression
--   high_expr: fragment — the adjusted-high expression
--   low_expr: fragment — the adjusted-low expression
--   close_expr: fragment — the adjusted-close expression
--   amt_select: fragment — the trading_amount expression (base column or margin join)
--   amt_joins: fragment — the trading_amount join clauses (etf/stock)
SELECT b.code,
       extract(epoch from b.date)::float8 AS date,
       {{open_expr}}::float8 AS open,
       {{high_expr}}::float8 AS high,
       {{low_expr}}::float8 AS low,
       {{close_expr}}::float8 AS close,
       {{amt_select}}::float8 AS trading_amount
FROM {{base_from}}
{{amt_joins}}
WHERE b.code = ANY({{codes}}::text[])
  AND b.close IS NOT NULL
  AND b.date >= {{since}}
  AND COALESCE(b.is_ohl_estimated, FALSE) = FALSE
ORDER BY b.code, b.date ASC
