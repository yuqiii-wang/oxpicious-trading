-- ============================================================================
--  Forecast Next Day — the ±kσ next-day scenario forecast register.
--  Written by the strategy.forecast_next_day pipeline (nightly strategy_daily
--  step 2, or the Strategy → Forecast page's Run Forecast button): per
--  (anchor bar date, code), the trailing-20d population σ of daily returns
--  projects six next-day closes (±1σ/±2σ/±3σ) and each scenario registers
--  WHICH singleton-strategy signal would trigger under that close — the
--  active analysis_signals.signal_rules cells of the close-derivable
--  families evaluated against the synthetic close (the live tier's breach
--  contract, projected).
--
--  Row grain: trigger_no 0 is the scenario SUMMARY row (no signal fired /
--  not comparable — signal columns NULL); trigger_no 1..n carries ONE fired
--  rule cell each. Scenario constants (base_close, σ, forecast_close,
--  change_pct) are denormalized onto every row of the scenario so per-signal
--  reads are self-contained.
--
--  PK: (anchor_date, sec_type, code, direction, sigma_mult, trigger_no)
--
--  Usage: psql -d analytics -f strategy/00_init.sql (master list)
-- ============================================================================

-- ----------------------------------------------------------------------------
--  Schema + grants (mirrors algo_configs.sql; idempotent so this file can
--  run standalone).
-- ----------------------------------------------------------------------------
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: Next-day ±kσ scenario forecast register — projected closes from the 20d return σ and which strategy signal would fire under each scenario.
-- tags: forecast, 预测, sigma scenarios, 波动率情景, signal trigger, 信号触发, reference, 参考

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
--  Table: strategy.forecast_next_day
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS strategy.forecast_next_day (
    anchor_date         DATE          NOT NULL,
    sec_type            SMALLINT      NOT NULL,
    code                TEXT          NOT NULL,
    direction           SMALLINT      NOT NULL,
    sigma_mult          SMALLINT      NOT NULL,
    trigger_no          SMALLINT      NOT NULL,
    base_close          NUMERIC(18,4) NOT NULL,
    sigma_20d_pct       NUMERIC(12,8) NOT NULL,
    forecast_close      NUMERIC(18,4) NOT NULL,
    forecast_change_pct NUMERIC(12,8) NOT NULL,
    signal_type         SMALLINT,
    signal_sub_type     SMALLINT,
    action              SMALLINT,
    signal_value        NUMERIC(18,6),
    signal_threshold    NUMERIC(18,6),
    signal_excess_pct   NUMERIC(12,6),
    confidence_bp       INTEGER,
    trend_state         SMALLINT,
    regime_state        SMALLINT,
    position_open       BOOLEAN,
    would_execute       BOOLEAN,
    strategy_name       TEXT          NOT NULL,
    created_at          TIMESTAMPTZ   NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ   NOT NULL DEFAULT now(),

    CONSTRAINT pk_forecast_next_day
        PRIMARY KEY (anchor_date, sec_type, code, direction, sigma_mult, trigger_no),
    CONSTRAINT chk_forecast_next_day_sigma_mult
        CHECK (sigma_mult BETWEEN 1 AND 3),
    CONSTRAINT chk_forecast_next_day_trigger_no
        CHECK (trigger_no >= 0),
    -- a fired-cell row (trigger_no > 0) must carry its signal identity; the
    -- summary row (trigger_no = 0) must NOT fire anything.
    CONSTRAINT chk_forecast_next_day_trigger_shape CHECK (
        (trigger_no = 0 AND signal_type IS NULL AND action IS NULL)
        OR (trigger_no > 0 AND signal_type IS NOT NULL
            AND signal_sub_type IS NOT NULL AND action IS NOT NULL)
    )
) PARTITION BY HASH (code);

-- Native hash partitions (8) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('strategy', 'forecast_next_day', 8);

-- Latest-anchor reads per code (the Forecast page's default view).
CREATE INDEX IF NOT EXISTS idx_forecast_next_day_code_anchor
    ON strategy.forecast_next_day (code, anchor_date DESC);

COMMENT ON TABLE  strategy.forecast_next_day IS
    'Next-day ±kσ scenario forecast register: projected closes from the 20d return σ + which strategy signal would trigger under each scenario (written by strategy.forecast_next_day).';
COMMENT ON COLUMN strategy.forecast_next_day.anchor_date IS
    'The REAL bar date the forecast is anchored on (the code''s latest OHLCV close).';
COMMENT ON COLUMN strategy.forecast_next_day.sec_type IS
    'static.label_dim domain sec_type (0 etf, 1 index, 2 stock, 3 industry).';
COMMENT ON COLUMN strategy.forecast_next_day.code IS
    'Security code (e.g. index code 000001).';
COMMENT ON COLUMN strategy.forecast_next_day.direction IS
    'static.label_dim domain forecast_direction (0 down, 1 up) — the scenario''s side of the σ move.';
COMMENT ON COLUMN strategy.forecast_next_day.sigma_mult IS
    'The σ multiplier k ∈ {1, 2, 3} of the projected next-day move.';
COMMENT ON COLUMN strategy.forecast_next_day.trigger_no IS
    '0 = the scenario summary row (no signal fired — signal columns NULL); 1..n = one fired rule cell per row.';
COMMENT ON COLUMN strategy.forecast_next_day.base_close IS
    'The anchor date''s official close.';
COMMENT ON COLUMN strategy.forecast_next_day.sigma_20d_pct IS
    'Population std (ddof=0, the bollinger convention) of the trailing 20 close-to-close pct returns at the anchor, in PERCENT.';
COMMENT ON COLUMN strategy.forecast_next_day.forecast_close IS
    'The synthetic next-day close = base_close × (1 ± k·σ) (σ in return units).';
COMMENT ON COLUMN strategy.forecast_next_day.forecast_change_pct IS
    'forecast_close / base_close − 1, in PERCENT (±k·sigma_20d_pct by construction).';
COMMENT ON COLUMN strategy.forecast_next_day.signal_type IS
    'static.label_dim domain signal_type — the fired cell''s family (NULL on the summary row).';
COMMENT ON COLUMN strategy.forecast_next_day.signal_sub_type IS
    'static.label_dim domain signal_sub_type — the fired cell''s sub type.';
COMMENT ON COLUMN strategy.forecast_next_day.action IS
    'static.label_dim domain action (0 buy, 1 sell) — the fired cell''s own action.';
COMMENT ON COLUMN strategy.forecast_next_day.signal_value IS
    'The scenario value in the cell''s bar space (e.g. the synthetic-day RSI, boll z, logret).';
COMMENT ON COLUMN strategy.forecast_next_day.signal_threshold IS
    'The cell''s resolved bar (from its rule_params.bar_def) under this scenario.';
COMMENT ON COLUMN strategy.forecast_next_day.signal_excess_pct IS
    '(signal_value − signal_threshold) / |signal_threshold| × 100.';
COMMENT ON COLUMN strategy.forecast_next_day.confidence_bp IS
    'The PROJECTED confidence in basis points — the live tier''s rules_confidence_bp formula (base_pct × depth scale × sample scale) evaluated at the synthetic value.';
COMMENT ON COLUMN strategy.forecast_next_day.trend_state IS
    'static.label_dim domain trend — the day-context trend used for the cell match (latest known at the anchor; tomorrow assumed = today, a documented approximation).';
COMMENT ON COLUMN strategy.forecast_next_day.regime_state IS
    'static.label_dim domain regime — the day-context regime used for the cell match (same latest-known approximation).';
COMMENT ON COLUMN strategy.forecast_next_day.position_open IS
    'TRUE when the strategy_name run''s latest decision for the code holds a position (the SELL pickup''s precondition).';
COMMENT ON COLUMN strategy.forecast_next_day.would_execute IS
    'The strategy pickup reading: buy events always execute (long-only entry); sell events only when a position is open past min_holding_period (position_open).';
COMMENT ON COLUMN strategy.forecast_next_day.strategy_name IS
    'The singleton strategy the trigger reading applies to (the nightly mixed portfolio by default).';
