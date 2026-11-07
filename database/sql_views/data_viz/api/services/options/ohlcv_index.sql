-- Underlying OHLCV, INDEX mode — stats.v_index_daily with the raw index
-- code (annual-sentiment panel). null-preserving: CN-holiday rows of
-- cross-border indices / estimated rows carry NULL OHLC or volume —
-- rendered as chart gaps, never 0. Serves GET /api/options/ohlcv.
-- args:
--   code: value text — the index code ($1)
--   date_where: fragment — optional " AND date >= $n::date" /
--     " AND date <= $n::date" bounds (numbering continues after $1)
      SELECT date, open, high, low, close, trading_shares
      FROM stats.v_index_daily
      WHERE code = {{code}}{{date_where}}
      ORDER BY date ASC
