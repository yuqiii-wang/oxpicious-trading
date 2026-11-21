-- Strategy backtest — ONE strategy_identity row by seq_id (the run-history
-- selector path: the UI already knows the seq, no latest-run lookup needed).
-- Same shape as _shared/seq so the service maps both identically.
-- args:
--   seq_id: value bigint — the strategy_identity.seq_id to load
  SELECT seq_id, seq_no, fault_tolerance, params
  FROM strategy.strategy_identity
  WHERE seq_id = {{seq_id}}
  LIMIT 1
