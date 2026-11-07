-- Dividend events for ONE stock code (accepts both suffixed and bare forms
-- via code variants — bare 6-digit codes would otherwise fall back to a
-- REGEXP_REPLACE full scan; idx_stock_dividends_code_exdate stays usable).
-- Serves GET /api/stock/daily (also the composition pie's per-stock OHLC
-- expansion).
-- args:
--   codes: value text[] — codeVariants(code)
  SELECT code, ex_dividend_date, record_date,
         dividend_per_share_pre_tax, dividend_per_share_post_tax,
         total_dividend_wan, pre_close_price, open_price
    FROM stats.stock_dividends
 WHERE code = ANY({{codes}}::text[]) ORDER BY ex_dividend_date ASC
