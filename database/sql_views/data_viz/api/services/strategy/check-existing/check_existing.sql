-- Check if a strategy run already exists for the natural key — the latest
-- strategy_identity row for (strategy_name, sec_type, code), preferring
-- the is_active run then the highest seq_no. Drives the UI's
-- force-rerun confirmation.
-- Serves GET /api/strategy/singleton/check.
-- args:
--   strategy_name: value text — algo name (binary) or portfolio name (mixed)
--   sec_type: value text — 'index' | 'etf' | 'stock'
--   code: value text — the traded security code
  SELECT seq_id, seq_no, start_date, end_date, scenario, status, fault_tolerance
  FROM strategy.strategy_identity
  WHERE strategy_name = {{strategy_name}}
    AND sec_type = {{sec_type}}
    AND code = {{code}}
  ORDER BY is_active DESC, seq_no DESC
  LIMIT 1