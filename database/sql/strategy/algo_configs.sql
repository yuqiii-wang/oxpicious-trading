-- ============================================================================
--  Algo Configs — per-(security, strategy, date-range) algo param overrides.
--  Stores the customizable params for a factors_and_algos algo (e.g.
--  macd) as a JSONB column, keyed by security + strategy + an
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
--  Usage: psql -d strategy -f strategy/00_init.sql (master list)
-- ============================================================================

-- ----------------------------------------------------------------------------
--  Schema + grants (mirrors trade_decision_seqs/00_schema.sql; idempotent so this
--  file can run standalone).
-- ----------------------------------------------------------------------------
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: Per-security, per-strategy parameter overrides for a date range — what a backtest loads on top of the algo defaults.
-- tags: algo configs, 算法参数, parameters, 参数, overrides, 覆盖, reference, 参考

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
    -- TRUE = the algo's DEFAULT_PARAMS row (reserved wide range
    -- 1900-01-01..9999-12-31, never overwritten by training); FALSE = a
    -- trained row written by _optm_engine persist.
    is_default      BOOLEAN       NOT NULL DEFAULT FALSE,

    CONSTRAINT pk_algo_configs
        PRIMARY KEY (sec_code, sec_type, strategy_name, start_date, end_date),
    -- end_date must not precede start_date (a range must be well-formed).
    CONSTRAINT chk_algo_configs_date_order CHECK (end_date >= start_date)
) PARTITION BY HASH (sec_code);

-- Native hash partitions (8) keyed by sec_code
-- Native hash partitions (8) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('strategy', 'algo_configs', 8);

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

-- ----------------------------------------------------------------------------
--  Backfill: pre-existing wide-range rows are promoted to is_default so
--  ensure_default_config keeps treating them as the (single) default row.
--  NOTE: asyncpg encodes Python date(9999,12,31) as PG 'infinity', so the
--  legacy wide rows carry end_date 'infinity' — the backfill matches BOTH
--  spellings.
-- ----------------------------------------------------------------------------
UPDATE strategy.algo_configs
   SET is_default = TRUE
 WHERE start_date = DATE '1900-01-01'
   AND end_date IN ('infinity', DATE '9999-12-31')
   AND NOT is_default;

COMMENT ON COLUMN strategy.algo_configs.is_default IS
    'TRUE = the algo DEFAULT_PARAMS row (reserved wide range 1900-01-01..9999-12-31, never overwritten by training). FALSE = a trained row [train_date, 9999-12-31] written by _optm_engine; the loader picks the latest start_date.';
