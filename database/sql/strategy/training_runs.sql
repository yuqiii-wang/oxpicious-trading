-- ============================================================================
--  Training Process — nested optimizer run history + trial log.
--
--  Persists what `python -m strategy.factors_and_algos._optm_engine`
--  (the UI "Train Model" button) does:
--
--    strategy.training_runs    one row per training run (header + outcome).
--                              status 'running' is inserted at start and
--                              flipped to 'completed' / 'failed' at exit,
--                              so crashed runs stay visible.
--    strategy.training_trials  one row per evaluated point, tagged by
--                              loss_type so the TWO regime losses can be
--                              displayed separately:
--                                'set_a_omega'   — Stage A signal params
--                                  (Optuna TPE trials; Omega ratio loss)
--                                'set_b_calmar'  — Stage B execution params
--                                  (per-candidate vanilla grid; Calmar loss)
--
--  Also augments strategy.algo_configs with IS_DEFAULT so the UI can show
--  the algo's DEFAULT_PARAMS row alongside the trained rows:
--    is_default = TRUE  — the reserved wide-range row [1900-01-01,
--                         9999-12-31] written by ensure_default_config;
--                         training NEVER overwrites it.
--    is_default = FALSE — a trained row [train_date, 9999-12-31] written
--                         by the optimizer; the loader (ORDER BY
--                         start_date DESC) picks the latest one, keeping
--                         user-authored dated rows in precedence.
--
--  Usage: psql -d strategy -f strategy/00_init.sql (master list; the
--  training tables live in training_runs.sql + training_trials.sql)
-- ============================================================================

-- ----------------------------------------------------------------------------
--  Schema + grants (mirrors 04_factors_and_algos.sql; idempotent).
-- ----------------------------------------------------------------------------
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: One strategy-training process — the log header of a parameter search for one security and strategy.
-- tags: training, 训练, parameter search, 参数搜索, process log, 过程日志

CREATE SCHEMA IF NOT EXISTS strategy;

GRANT USAGE ON SCHEMA strategy TO public;
GRANT USAGE ON SCHEMA strategy TO anon;
GRANT USAGE ON SCHEMA strategy TO authenticated;
GRANT USAGE ON SCHEMA strategy TO service_role;

ALTER DEFAULT PRIVILEGES IN SCHEMA strategy GRANT ALL ON TABLES TO public;
ALTER DEFAULT PRIVILEGES IN SCHEMA strategy GRANT ALL ON SEQUENCES TO public;

ALTER DEFAULT PRIVILEGES IN SCHEMA strategy GRANT SELECT ON TABLES TO anon;
ALTER DEFAULT PRIVILEGES IN SCHEMA strategy GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA strategy GRANT ALL ON TABLES TO service_role;

ALTER DEFAULT PRIVILEGES IN SCHEMA strategy GRANT USAGE, SELECT ON SEQUENCES TO anon;
ALTER DEFAULT PRIVILEGES IN SCHEMA strategy GRANT USAGE, SELECT ON SEQUENCES TO authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA strategy GRANT ALL ON SEQUENCES TO service_role;

GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA strategy TO postgres;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA strategy TO postgres;

-- ----------------------------------------------------------------------------
--  Table: strategy.training_runs — one row per Train Model invocation.
--
--  NOT hash-partitioned: process-log registry keyed by a surrogate IDENTITY
--  (run_id). Partitioning by the identity column itself would only scatter an
--  append-only log; lookups go through idx_training_runs_key
--  (sec_type, sec_code, strategy_name, started_at DESC). The per-trial FACT
--  table training_trials below IS hash-partitioned by run_id.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS strategy.training_runs (
    run_id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    sec_type        TEXT          NOT NULL DEFAULT 'index'
        CHECK (sec_type IN ('index', 'etf', 'stock')),
    sec_code        TEXT          NOT NULL,
    strategy_name   TEXT          NOT NULL,
    -- Study inputs (CLI args snapshot)
    trials          INTEGER       NOT NULL,
    top_k           INTEGER       NOT NULL,
    seed            INTEGER,
    oos_frac        REAL          NOT NULL DEFAULT 0.2,
    statics         JSONB         NOT NULL DEFAULT '{}'::jsonb,
    gpu_mode        TEXT          NOT NULL DEFAULT 'auto',
    -- Lifecycle
    status          TEXT          NOT NULL DEFAULT 'running'
        CHECK (status IN ('running', 'completed', 'failed')),
    started_at      TIMESTAMPTZ   NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ,
    error_text      TEXT,
    -- Outcome (step 5 winner)
    winner_trial_no INTEGER,
    n_candidates    INTEGER,
    grid_size       INTEGER,
    best_params     JSONB,   -- combined Set A ∪ Set B (what got upserted)
    best_a_params   JSONB,   -- Set A signal params
    best_b_params   JSONB,   -- Set B execution params
    best_a_metrics  JSONB,   -- OmegaLoss bundle of the winner
    best_b_metrics  JSONB,   -- CalmarLoss bundle of the winner
    kelly           JSONB,   -- analytical Kelly outcome (reported static)
    full_series_metrics JSONB, -- full-series sanity check (report only)
    log_text        TEXT     -- captured human-readable training log lines
);

CREATE INDEX IF NOT EXISTS idx_training_runs_key
    ON strategy.training_runs (sec_type, sec_code, strategy_name, started_at DESC);

COMMENT ON TABLE  strategy.training_runs IS
    'One row per "Train Model" run (_optm_engine nested TPE→Kelly→grid study). Written by training_store.py.';
COMMENT ON COLUMN strategy.training_runs.status IS
    'running → completed | failed (a crashed run stays running/failed so it is visible in the UI).';
COMMENT ON COLUMN strategy.training_runs.log_text IS
    'The captured stdout log lines of the run (step banners, trial lines, Kelly lines).';
