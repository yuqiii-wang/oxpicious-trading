-- Composite-industry baseline rows + label for the shared baseline-plot
-- component (pool-'all' slice of stats.industry_basic_stats; window MAs
-- over the composite close, partial windows per the builds'
-- min_periods=1 convention; pe = mean_pe). Serves GET /api/sec-baseline
-- (sec_type=industry).
-- args:
--   industry_code: value text — the industry_id (= code) to bundle
  WITH src AS (
    SELECT b.date, b.industry_id,
           b.mean_open AS open, b.mean_high AS high,
           b.mean_low AS low, b.mean_close AS close,
           b.total_trading_amount AS trading_amount,
           b.mean_pe AS pe
    FROM stats.industry_basic_stats b
    WHERE b.industry_id = {{industry_code}} AND b.pool_size = 3 AND b.mean_close IS NOT NULL
  )
  SELECT
    (SELECT name FROM stats.industry_identity
      WHERE code = {{industry_code}} ORDER BY date DESC LIMIT 1) AS name,
    to_char(s.date, 'YYYY-MM-DD') AS date,
    s.open::float8, s.high::float8, s.low::float8, s.close::float8,
    s.trading_amount::float8, s.pe::float8,
    AVG(s.close) OVER (PARTITION BY s.industry_id ORDER BY s.date
      ROWS BETWEEN 4 PRECEDING AND CURRENT ROW)::float8 AS ma5,
    AVG(s.close) OVER (PARTITION BY s.industry_id ORDER BY s.date
      ROWS BETWEEN 19 PRECEDING AND CURRENT ROW)::float8 AS ma20,
    AVG(s.close) OVER (PARTITION BY s.industry_id ORDER BY s.date
      ROWS BETWEEN 59 PRECEDING AND CURRENT ROW)::float8 AS ma60,
    AVG(s.close) OVER (PARTITION BY s.industry_id ORDER BY s.date
      ROWS BETWEEN 119 PRECEDING AND CURRENT ROW)::float8 AS ma120,
    AVG(s.close) OVER (PARTITION BY s.industry_id ORDER BY s.date
      ROWS BETWEEN 254 PRECEDING AND CURRENT ROW)::float8 AS ma255
  FROM src s
  ORDER BY s.date
