-- Strategy backtest — latest strategy_seq for ONE code + sec_type +
-- strategy_name (binary algo name, or "portfolio:bb*0.5+macd*0.5" mixed
-- mode). is_active runs win the default load; seq_no DESC is the fallback.
-- Consumed by services/strategy/backtest.ts.
-- args:
--   sec_type: value smallint — static.label_dim 'sec_type' code
--   code: value text — the strategy code
--   strategy_name: value text — algo or portfolio name
  SELECT seq_id, fault_tolerance
  FROM strategy.strategy_identity
  WHERE sec_type = {{sec_type}} AND code = {{code}} AND strategy_name = {{strategy_name}}
  ORDER BY is_active DESC, seq_no DESC
  LIMIT 1
