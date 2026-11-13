
-- ----------------------------------------------------------------------------
--  Table: strategy.training_trials — one row per evaluated point.
--    loss_type separates the TWO regime losses for display:
--      'set_a_omega'  — Stage A TPE trials (trial_no = Optuna number,
--                       grid_idx fixed 0). metrics: omega, loss,
--                       positive_month_fraction, n_trades, ...
--      'set_b_calmar' — Stage B grid points (trial_no = the candidate's
--                       Stage A trial number, grid_idx = 1..grid_size).
--                       metrics: calmar, loss, total_return, max_dd_pct, ...
-- ----------------------------------------------------------------------------
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: One training run's per-candidate trials — each grid point's parameters and backtest metrics.
-- tags: training, 训练, trials, 试验, grid search, 网格搜索, metrics, 指标

CREATE TABLE IF NOT EXISTS strategy.training_trials (
    run_id          BIGINT      NOT NULL REFERENCES strategy.training_runs(run_id)
                                ON DELETE CASCADE,
    loss_type       TEXT        NOT NULL
        CHECK (loss_type IN ('set_a_omega', 'set_b_calmar')),
    trial_no        INTEGER     NOT NULL,
    grid_idx        INTEGER     NOT NULL DEFAULT 0,
    params          JSONB       NOT NULL DEFAULT '{}'::jsonb,
    metrics         JSONB       NOT NULL DEFAULT '{}'::jsonb,
    loss            DOUBLE PRECISION NOT NULL,
    constraint_ok   BOOLEAN     NOT NULL DEFAULT FALSE,
    no_trades       BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT pk_training_trials
        PRIMARY KEY (run_id, loss_type, trial_no, grid_idx)
) PARTITION BY HASH (run_id);

-- Native hash partitions (8) keyed by run_id
-- Native hash partitions (8) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('strategy', 'training_trials', 8);

CREATE INDEX IF NOT EXISTS idx_training_trials_run
    ON strategy.training_trials (run_id, loss_type, loss);

COMMENT ON TABLE  strategy.training_trials IS
    'Per-point training log: every Stage A TPE trial and every Stage B grid evaluation, tagged by loss_type.';
COMMENT ON COLUMN strategy.training_trials.loss_type IS
    'set_a_omega = Stage A signal loss (Omega ratio); set_b_calmar = Stage B execution loss (Calmar ratio).';
COMMENT ON COLUMN strategy.training_trials.trial_no IS
    'Stage A: the Optuna trial number. Stage B: the candidate''s Stage A trial number.';
COMMENT ON COLUMN strategy.training_trials.grid_idx IS
    'Stage B: the 1..N grid-point sequence within the candidate''s grid. Stage A: fixed 0.';
