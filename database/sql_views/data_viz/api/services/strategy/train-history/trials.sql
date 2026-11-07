-- Train Model history — per-point trial log of ONE run for BOTH loss
-- types ('set_a_omega' Stage A TPE trials, 'set_b_calmar' Stage B grid
-- points); the UI displays them separately.
-- Serves GET /api/strategy/singleton/train-info (?run_id pick).
-- args:
--   run_id: value int — the strategy.training_runs run to list trials for
  SELECT run_id, loss_type, trial_no, grid_idx, params, metrics, loss,
         constraint_ok, no_trades
  FROM strategy.training_trials
  WHERE run_id = {{run_id}}
  ORDER BY loss_type DESC, trial_no, grid_idx