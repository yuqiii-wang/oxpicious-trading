-- Margin Trends 'etf' attribution — per-(etf, date) margin series from
-- stats.etf_liquidity_margin for the industry's ETFs (via
-- sec_classification parent_index_is_primary), with the ETF close and the
-- decoded regime label joined LATERALLY per row.
-- Serves GET /api/analysis/margin-trends/industry-series?attribution=etf
-- args:
--   industry_id: value text — the industry to list the series for
--   regime_decode: fragment — labelDecodeSql("regime", "r.regime")
  SELECT e.code, e.date, e.rz_balance, e.rz_buy, e.trading_amount, eb.close,
         mr.regime
  FROM stats.etf_liquidity_margin e
  LEFT JOIN LATERAL (
    SELECT close FROM stats.etf_basic_stats b
    WHERE b.code = e.code AND b.date = e.date
    LIMIT 1
  ) eb ON TRUE
  LEFT JOIN LATERAL (
    SELECT {{regime_decode}} AS regime
    FROM stats.market_regimes r
    -- sec_type stores its static.label_dim SMALLINT code (0 = etf)
    WHERE r.code = e.code AND r.date = e.date AND r.sec_type = 0
    LIMIT 1
  ) mr ON TRUE
  WHERE e.code IN (
    SELECT sc.code FROM stats.sec_classification sc
    WHERE sc.type = 'etf'
      AND sc.industry_id = {{industry_id}}::text
      AND sc.parent_index_is_primary = TRUE
      AND sc.is_active = TRUE
  )
  ORDER BY e.code, e.date
