-- Margin Trends 'index' attribution — per-(security, date) margin series
-- for ONE industry from the analysis.margin_index_series TABLE
-- (weighted-avg constituent-stock margin), with the composite
-- close/trading_amount and the decoded regime label joined LATERALLY per
-- row (a plain JOIN made the planner hash-join and seq-scan the whole
-- basic-stats table).
-- Serves GET /api/analysis/margin-trends/industry-series?attribution=index
-- args:
--   industry_id: value text — the industry to list the series for
--   regime_decode: fragment — labelDecodeSql("regime", "r.regime")
  SELECT s.index_code, s.date, s.index_margin_balance, s.index_margin_buy,
         ib.close, ib.trading_amount, mr.regime
  FROM analysis.margin_index_series s
  LEFT JOIN LATERAL (
    SELECT close, trading_amount FROM stats.index_basic_stats b
    WHERE b.code = s.index_code AND b.date = s.date
    LIMIT 1
  ) ib ON TRUE
  LEFT JOIN LATERAL (
    SELECT {{regime_decode}} AS regime
    FROM stats.market_regimes r
    -- sec_type stores its static.label_dim SMALLINT code (1 = index)
    WHERE r.code = s.index_code AND r.date = s.date AND r.sec_type = 1
    LIMIT 1
  ) mr ON TRUE
  WHERE s.industry_id = {{industry_id}}::text
  ORDER BY s.index_code, s.date
