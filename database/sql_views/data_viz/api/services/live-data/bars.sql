-- Intraday bars for the page's codes on ONE date — grouped by code
-- client-side (the page lists multiple codes per page). The volume column
-- exists only on the stock table; indices emit NULL so the bar type stays
-- uniform. Serves GET /api/live-data combined.
-- args:
--   codes: value text[] — the page's codes
--   date: value text — trading day (YYYY-MM-DD)
--   intraday_table: fragment — TABLES[sec_type].table
--   volume_expr: fragment — "trading_shares AS volume" (stock) /
--     "NULL::numeric AS volume" (index)
SELECT code, to_char(time, 'HH24:MI:SS') AS time,
            open, high, low, close, {{volume_expr}}, change, change_pct
       FROM {{intraday_table}}
      WHERE code = ANY({{codes}}::text[]) AND date = {{date}}::date
      ORDER BY code, time ASC
