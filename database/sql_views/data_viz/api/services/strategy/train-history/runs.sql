-- Train Model history — one header row per Train Model click
-- (strategy.training_runs: status + step-5 winner outcome), newest first.
-- Serves GET /api/strategy/singleton/train-info.
-- args:
--   sec_type: value text — 'index' | 'etf' | 'stock'
--   sec_code: value text — the traded security code
--   strategy_name: value text — algo name (binary) or portfolio name (mixed)
  SELECT run_id, status, trials, top_k, seed, oos_frac,
         started_at, finished_at, winner_trial_no, n_candidates, grid_size,
         best_params, best_a_metrics, best_b_metrics, kelly, error_text
  FROM strategy.training_runs
  WHERE sec_type = {{sec_type}} AND sec_code = {{sec_code}} AND strategy_name = {{strategy_name}}
  ORDER BY started_at DESC
  LIMIT 10