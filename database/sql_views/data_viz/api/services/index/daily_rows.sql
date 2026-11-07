-- Daily rows of ONE index from stats.v_index_daily with an optional
-- date-range filter (index-driven WHERE). Serves GET /api/index/daily.
-- args:
--   code: value text — the index code ($1)
--   date_where: fragment — optional " AND date >= $n::date" /
--     " AND date <= $n::date" bounds (numbering continues after $1)

    SELECT date, open, high, low, close, trading_shares, trading_amount, change_pct,
           pe, cons_number, ma5, ma20, ma60, ma120, ma255, has_intraday_5mins
      FROM stats.v_index_daily
     WHERE code = {{code}}{{date_where}}
     ORDER BY date ASC
