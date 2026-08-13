-- ============================================================================
--  Algo Configs — per-(security, strategy, date-range) algo param overrides.
--  Stores the customizable params for a factors_and_algos algo (e.g.
--  bollinger_bands) as a JSONB column, keyed by security + strategy + an
--  active date range. Lets a strategy load algo config dynamically from the
--  DB instead of hardcoding it in Python.
--
--  PK: (sec_type, sec_code, strategy_name, start_date, end_date)
--      Multiple non-overlapping ranges may exist per (sec_type, sec_code,
--      strategy_name); the loader picks the row whose [start_date, end_date]
--      contains the target date (default: today).
--
--  Table: strategy.algo_configs
--    params JSONB — algo-specific overrides merged over the algo's
--                   DEFAULT_PARAMS via factors_and_algos.<algo>.build_params.
--                   Trading-layer keys (buy_notional, min_holding_period, ...)
--                   may also travel here and pass through to the engine.
--
--  Usage: psql -d strategy -f strategy/04_factors_and_algos.sql
-- ============================================================================

-- ----------------------------------------------------------------------------
--  Schema + grants (mirrors 01_trade_decision_seqs.sql; idempotent so this
--  file can run standalone).
-- ----------------------------------------------------------------------------
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
--  Table: strategy.algo_configs
--    One row per (security, strategy, date range). The params JSONB carries
--    the algo-specific overrides for that range; a strategy loads the active
--    row (range contains target_date) and merges it over the algo's
--    DEFAULT_PARAMS via build_params().
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS strategy.algo_configs (
    sec_type        TEXT          NOT NULL DEFAULT 'index'
        CHECK (sec_type IN ('index', 'etf', 'stock')),
    sec_code        TEXT          NOT NULL,
    strategy_name   TEXT          NOT NULL,
    start_date      DATE          NOT NULL,
    end_date        DATE          NOT NULL,
    params          JSONB         NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ   NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ   NOT NULL DEFAULT now(),

    CONSTRAINT pk_algo_configs
        PRIMARY KEY (sec_type, sec_code, strategy_name, start_date, end_date),
    -- end_date must not precede start_date (a range must be well-formed).
    CONSTRAINT chk_algo_configs_date_order CHECK (end_date >= start_date)
);

-- Lookup index: find the active row for a (sec_type, sec_code, strategy_name)
-- at a given target date. The PK already covers an exact equality lookup on
-- all five PK columns; this partial index accelerates the common
-- "target_date BETWEEN start_date AND end_date" range probe.
CREATE INDEX IF NOT EXISTS idx_algo_configs_active_range
    ON strategy.algo_configs (sec_type, sec_code, strategy_name, start_date, end_date);

COMMENT ON TABLE  strategy.algo_configs IS
    'Per-(security, strategy, date-range) algo param overrides (JSONB). Loaded by factors_and_algos.loader.load_algo_config.';
COMMENT ON COLUMN strategy.algo_configs.sec_type IS
    'index | etf | stock (mirrors strategy_identity.sec_type).';
COMMENT ON COLUMN strategy.algo_configs.sec_code IS
    'Security code (e.g. index code 000922). Distinct name from strategy_identity.code but same meaning.';
COMMENT ON COLUMN strategy.algo_configs.strategy_name IS
    'Strategy package name (e.g. singleton_trading) — the consumer of this algo config.';
COMMENT ON COLUMN strategy.algo_configs.params IS
    'Algo-specific param overrides (JSONB). Merged over the algo DEFAULT_PARAMS via factors_and_algos.<algo>.build_params. May also carry trading-layer keys (buy_notional, min_holding_period, ...) that pass through to the engine.';
COMMENT ON CONSTRAINT chk_algo_configs_date_order ON strategy.algo_configs IS
    'end_date must be >= start_date (date range must be well-formed).';
