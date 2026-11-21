-- Train Model history — the TRAINED algo_configs rows ([train_date,
-- 9999-12-31] wide ranges written by each study), newest first.
-- Serves GET /api/strategy/singleton/train-info.
-- args:
--   sec_type: value text — 'index' | 'etf' | 'stock'
--   sec_code: value text — the traded security code
--   strategy_name: value text — algo name (binary) or portfolio name (mixed)
  SELECT start_date, end_date, params, updated_at
  FROM strategy.algo_configs
  WHERE sec_type = {{sec_type}} AND sec_code = {{sec_code}} AND strategy_name = {{strategy_name}}
    AND NOT is_default
  ORDER BY start_date DESC, updated_at DESC
  LIMIT 10