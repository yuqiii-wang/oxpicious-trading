-- Row data for the page's stock codes (with optional date filtering) —
-- BASE TABLES + per-table code predicates, same rationale as daily_rows.sql
-- (the view's LEFT JOINs defeat ANY-array pushdown and seq-scan/sort the
-- full sub-tables). pageCodes carry the exchange suffix, so the codes match
-- directly. WHERE b.close IS NOT NULL skips bogus identity-only entries
-- ("test"/"test2"). Serves GET /api/stock/combined (also the shared
-- baseline bundle via sec-baseline.service).
-- args:
--   codes: value text[] — the page's stock codes ($1)
--   date_where: fragment — optional " AND i.date >= $n::date" /
--     " AND i.date <= $n::date" bounds (numbering continues after $1)

    SELECT i.code, i.date, i.name, b.prev_close, b.open, b.high, b.low, b.close,
           b.pct_change, b.pe, b.eps, b.is_pe_estimated, b.has_intraday_5mins,
           lm.trading_shares, lm.trading_amount,
           lm.rz_balance, lm.rz_buy, lm.rq_balance_qty, lm.rq_balance_amt, lm.total_balance
      FROM stats.stock_identity i
      JOIN stats.stock_basic_stats b
        ON b.date = i.date AND b.code = i.code AND b.code = ANY({{codes}}::text[])
      LEFT JOIN stats.stock_liquidity_margin lm
        ON lm.date = i.date AND lm.code = i.code AND lm.code = ANY({{codes}}::text[])
     WHERE i.code = ANY({{codes}}::text[]) AND b.close IS NOT NULL{{date_where}}
     ORDER BY i.code, i.date ASC
