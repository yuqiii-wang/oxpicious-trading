-- Train Model history — one header row per Train Model click
-- (strategy.training_runs: status + step-5 winner outcome), newest first.
-- Serves GET /api/strategy/singleton/train-info.
-- The n_set_* counts are the REAL per-loss-type trial counts (the header's
-- `trials` column is the --trials CLI arg, meaningless for mixed runs —
-- the UI labels a run by what actually ran: TPE trials vs comb cells).
-- args:
--   sec_type: value text — 'index' | 'etf' | 'stock'
--   sec_code: value text — the traded security code
--   strategy_name: value text — algo name (binary) or portfolio name (mixed)
  SELECT r.run_id, r.status, r.trials, r.top_k, r.seed, r.oos_frac,
         r.started_at, r.finished_at, r.winner_trial_no, r.n_candidates,
         r.grid_size, r.best_params, r.best_a_metrics, r.best_b_metrics,
         r.kelly, r.error_text, r.full_series_metrics,
         (SELECT count(*) FROM strategy.training_trials t
          WHERE t.run_id = r.run_id AND t.loss_type = 'set_a_omega') AS n_set_a,
         (SELECT count(*) FROM strategy.training_trials t
          WHERE t.run_id = r.run_id AND t.loss_type = 'set_c_calmar') AS n_set_c,
         (SELECT count(*) FROM strategy.training_trials t
          WHERE t.run_id = r.run_id AND t.loss_type = 'set_b_calmar') AS n_set_b
  FROM strategy.training_runs r
  WHERE r.sec_type = {{sec_type}} AND r.sec_code = {{sec_code}} AND r.strategy_name = {{strategy_name}}
  ORDER BY r.started_at DESC
  LIMIT 10