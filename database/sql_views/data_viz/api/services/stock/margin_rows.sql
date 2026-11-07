-- Margin block — stock_liquidity_margin only for ONE stock. Dates without
-- margin activity are absent from the rows; the frontend maps by date and
-- renders nulls as line breaks (best-effort overlay — its failure is
-- "overlay absent", never a chart error). Serves GET /api/stock/margin.
-- args:
--   codes: value text[] — codeVariants(code) ($1)
--   date_where: fragment — optional " AND date >= $n::date" /
--     " AND date <= $n::date" bounds (numbering continues after $1)

    SELECT date, code, trading_shares, trading_amount,
           rz_balance, rz_buy, rq_balance_qty, rq_balance_amt, total_balance
      FROM stats.stock_liquidity_margin
     WHERE code = ANY({{codes}}::text[]){{date_where}}
     ORDER BY date ASC
