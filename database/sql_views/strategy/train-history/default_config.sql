-- Train Model history — the DEFAULT algo_configs row (is_default: the
-- algo's DEFAULT_PARAMS + reserved wide date range) for one
-- (sec_type, sec_code, strategy_name).
-- Serves GET /api/strategy/singleton/train-info.
-- args:
--   sec_type: value text — 'index' | 'etf' | 'stock'
--   sec_code: value text — the traded security code
--   strategy_name: value text — algo name (binary) or portfolio name (mixed)
  SELECT start_date, end_date, params, updated_at
  FROM strategy.algo_configs
  WHERE sec_type = {{sec_type}} AND sec_code = {{sec_code}} AND strategy_name = {{strategy_name}}
    AND is_default
  ORDER BY start_date
  LIMIT 1