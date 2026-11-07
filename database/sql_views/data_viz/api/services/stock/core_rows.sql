-- Core block — identity ⋈ basic_stats: OHLC + pct_change + PE/EPS for ONE
-- stock. INNER join on basic_stats is equivalent to the old view's LEFT
-- JOIN + `close IS NOT NULL` filter (identity-only rows are dropped either
-- way). has_intraday_5mins intentionally not selected — only the index
-- panel consumes it. The trend chart loads core + margin + dividends in
-- PARALLEL and joins them client-side by date. Serves GET /api/stock/core.
-- args:
--   codes: value text[] — codeVariants(code) ($1)
--   date_where: fragment — optional " AND i.date >= $n::date" /
--     " AND i.date <= $n::date" bounds (numbering continues after $1)

    SELECT i.date, i.code, i.name, b.prev_close, b.open, b.high, b.low, b.close,
           b.pct_change, b.pe, b.eps, b.is_pe_estimated
      FROM stats.stock_identity i
      JOIN stats.stock_basic_stats b
        ON b.date = i.date AND b.code = i.code AND b.code = ANY({{codes}}::text[])
     WHERE i.code = ANY({{codes}}::text[]) AND b.close IS NOT NULL{{date_where}}
     ORDER BY i.date ASC
