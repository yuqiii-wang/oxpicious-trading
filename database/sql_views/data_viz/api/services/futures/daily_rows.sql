-- All daily rows for ONE product (identity LEFT JOIN basic_stats) — the
-- combined response's raw rows (they additionally carry trading_shares for
-- the continuity measure). Serves GET /api/futures combined.
-- args:
--   product_code: value text — the product to fetch

    SELECT
      i.date,
      i.code,
      i.days_to_expiry,
      b.settlement_price,
      b.close,
      b.trading_amount,
      b.trading_shares,
      b.open_interest
    FROM stats.futures_identity i
    LEFT JOIN stats.futures_basic_stats b
      ON i.date = b.date AND i.code = b.code
    WHERE i.product_code = {{product_code}}
    ORDER BY i.date, i.code
