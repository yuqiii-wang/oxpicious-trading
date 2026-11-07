-- Daily rows of ONE stock — BASE TABLES, NOT stats.v_stock_daily:
-- `code = ANY($1)` (ScalarArrayOp) cannot enter an equivalence class, so
-- Postgres never pushed it through the view's LEFT JOINs — basic_stats
-- hash-joined via full seq scan and liquidity_margin fully seq-scanned +
-- externally sorted (479MB spill, ~35s per read 2026-09-29). Repeating the
-- predicate on EACH table's join clause gives the planner a restriction
-- qual everywhere → PK bitmap/nested-loop scans with hash-partition pruning
-- (~16ms measured). WHERE b.close IS NOT NULL skips rows with NULL OHLC
-- (identity-only rows like the historical "test"/"test2" entries) and makes
-- the basic_stats join INNER — equivalent to the view's LEFT JOIN + filter.
-- Serves GET /api/stock/daily.
-- args:
--   codes: value text[] — codeVariants(code) ($1)
--   date_where: fragment — optional " AND i.date >= $n::date" /
--     " AND i.date <= $n::date" bounds (numbering continues after $1)

    SELECT i.date, i.code, i.name, b.prev_close, b.open, b.high, b.low, b.close,
           b.pct_change, b.pe, b.eps, b.is_pe_estimated, b.has_intraday_5mins,
           lm.trading_shares, lm.trading_amount,
           lm.rz_balance, lm.rz_buy, lm.rq_balance_qty, lm.rq_balance_amt, lm.total_balance
      FROM stats.stock_identity i
      JOIN stats.stock_basic_stats b
        ON b.date = i.date AND b.code = i.code AND b.code = ANY({{codes}}::text[])
      LEFT JOIN stats.stock_liquidity_margin lm
        ON lm.date = i.date AND lm.code = i.code AND lm.code = ANY({{codes}}::text[])
     WHERE i.code = ANY({{codes}}::text[]) AND b.close IS NOT NULL{{date_where}}
     ORDER BY i.date ASC
