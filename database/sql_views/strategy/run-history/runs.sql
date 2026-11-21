-- Run history — the recent strategy_identity runs for ONE (sec_type, code,
-- strategy_name), newest first. Old runs are RETAINED with is_active=FALSE
-- when a newer run supersedes them (upsert_strategy_seq never deletes
-- non-forced history), so this list IS the run history the UI selector
-- displays. params carries the run's config (incl. the comb block +
-- params_version on portfolio runs) so different configs are visible.
-- Serves GET /api/strategy/singleton/run-history.
-- args:
--   sec_type: value text — 'index' | 'etf' | 'stock'
--   code: value text — the traded security code
--   strategy_name: value text — algo name (binary) or portfolio name (mixed)
  SELECT seq_id, seq_no, strategy_name, sec_type, code,
         start_date, end_date, params, scenario, status,
         fault_tolerance, is_active, created_at
  FROM strategy.strategy_identity
  WHERE sec_type = {{sec_type}} AND code = {{code}} AND strategy_name = {{strategy_name}}
  ORDER BY created_at DESC, seq_no DESC
  LIMIT 20
