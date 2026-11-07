-- Underlying OHLCV, ETF mode (default) — stats.v_etf_margin with the
-- native ETF code (split-adjusted prices when available; the service picks
-- adjusted vs raw per row). Serves GET /api/options/ohlcv.
-- args:
--   codes: value text[] — codeVariants(targetCode) ($1)
--   date_where: fragment — optional " AND date >= $n::date" /
--     " AND date <= $n::date" bounds (numbering continues after $1)
    SELECT date, adj_open, adj_high, adj_low, adj_close,
           open, high, low, close, trading_shares
    FROM stats.v_etf_margin
    WHERE code = ANY({{codes}}::text[]){{date_where}}
    ORDER BY date ASC
