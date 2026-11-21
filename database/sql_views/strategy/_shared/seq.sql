-- Strategy backtest — latest strategy_seq for ONE code + sec_type +
-- strategy_name (binary algo name, or "portfolio:bb+macd" mixed mode —
-- the ft-suffixed run-identity name, e.g. "macd_ft10"). is_active runs
-- win the default load; seq_no DESC is the fallback.
-- params is the run's stored config (echoed in the API response so the UI
-- can show which config produced the displayed run; includes the
-- config_source provenance block).
-- Consumed by services/strategy/backtest.ts.
-- args:
--   sec_type: value text — the sec_type string ('index' | 'etf' | 'stock')
--   code: value text — the strategy code
--   strategy_name: value text — algo or portfolio name
  SELECT seq_id, seq_no, fault_tolerance, params
  FROM strategy.strategy_identity
  WHERE sec_type = {{sec_type}} AND code = {{code}} AND strategy_name = {{strategy_name}}
  ORDER BY is_active DESC, seq_no DESC
  LIMIT 1
