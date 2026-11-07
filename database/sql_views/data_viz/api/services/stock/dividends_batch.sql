-- Dividend events for MANY stock codes at once (used by
-- getStocksCombined's batch fetch — small N, usually 1-2 codes per page).
-- args:
--   codes: value text[] — the page's stock codes
  SELECT code, ex_dividend_date, record_date,
         dividend_per_share_pre_tax, dividend_per_share_post_tax,
         total_dividend_wan, pre_close_price, open_price
    FROM stats.stock_dividends
 WHERE code = ANY({{codes}}::text[]) ORDER BY code, ex_dividend_date ASC
