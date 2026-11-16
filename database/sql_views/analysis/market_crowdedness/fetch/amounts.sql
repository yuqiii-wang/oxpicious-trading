-- Market crowdedness — per-stock daily trading amount in yuan (stats.
-- stock_liquidity_margin) for the compute window; trading_amount > 0
-- drops the streaming placeholder rows and suspended stocks (they are
-- outside the day's active pool by definition).
-- args:
--   stock_codes: value text[] — the sorted member stock codes
--   lo: value date — the window's lower bound
--   hi: value date — the window's upper bound
    SELECT code AS stock_code,
           extract(epoch from date)::float8 AS date,
           trading_amount::float8 AS amt
    FROM stats.stock_liquidity_margin
    WHERE code = ANY({{stock_codes}}::text[])
      AND trading_amount > 0
      AND date >= {{lo}}::date
      AND date <= {{hi}}::date
    ORDER BY code, date ASC
