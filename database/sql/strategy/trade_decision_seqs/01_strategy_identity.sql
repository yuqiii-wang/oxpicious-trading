-- ============================================================================
--  Trade Decision Sequences — strategy.strategy_identity
--  One row per strategy execution on ONE code (pure IDENTITY table).
--  Part of strategy/trade_decision_seqs/ (applied in order by
--  strategy/00_init.sql; overview in 00_schema.sql).
-- ============================================================================

-- ----------------------------------------------------------------------------
-- Table: strategy_identity
--   One row per strategy execution on ONE code. PURE IDENTITY table — run
--   RESULTS (dates, total_buy_cost, first-buy anchor, P&L summary) live on
--   strategy_results (1:1). Splitting identity from results keeps strategy_identity
--   stable across recomputes and concentrates display fields in one place.
--
--   NOT hash-partitioned: PostgreSQL requires every UNIQUE constraint on a
--   partitioned table to include the partition key. The natural business key
--   uq_strategy_identity_natural (strategy_name, sec_type, code, start_date,
--   end_date) excludes seq_id, so partitioning by seq_id would force dropping
--   that uniqueness (which the idempotent find_seq_id re-run logic depends
--   on). Tiny registry table (~15MB). All seq_id-keyed child fact tables
--   below ARE hash-partitioned by seq_id.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS strategy.strategy_identity (
    seq_id                BIGINT        GENERATED ALWAYS AS IDENTITY,
    strategy_name         TEXT          NOT NULL,
    seq_no                INTEGER       NOT NULL DEFAULT 1,
    sec_type              TEXT          NOT NULL DEFAULT 'index'
        CHECK (sec_type IN ('index', 'etf', 'stock')),
    code                  TEXT          NOT NULL,
    -- start_date / end_date: the OHLC period the strategy is run over
    -- (df.date.min() .. df.date.max()). These are part of the NATURAL
    -- business key so a re-run over the SAME period is idempotent (skip
    -- via find_seq_id), while a run over a DIFFERENT period gets its own
    -- seq. end_date NULL = open-ended (rare; the engine normally pins the
    -- last OHLC date). Mirrors strategy_results.start/end_date but those
    -- are the OUTPUT (min/max exec_date); these are the INPUT period.
    start_date            DATE          NOT NULL,
    end_date              DATE,
    params                JSONB         NOT NULL DEFAULT '{}'::jsonb,
    status                TEXT          NOT NULL DEFAULT 'completed'
        CHECK (status IN ('running', 'completed', 'stopped', 'error')),
    is_active             BOOLEAN       NOT NULL DEFAULT TRUE,
    created_at            TIMESTAMPTZ   NOT NULL DEFAULT now(),
    -- Fault tolerance percentage (0-20) applied to decision-day OHLC
    -- stress; 0 = baseline (no stress). Encoded in strategy_name as _ft{N}.
    fault_tolerance       NUMERIC(5,2)  NOT NULL DEFAULT 0,

    CONSTRAINT pk_strategy_identity PRIMARY KEY (seq_id)
);

-- Natural business key uniqueness (strategy_name, sec_type, code,
-- start_date, end_date) as a standalone unique index: a re-run over the
-- SAME period is a skip, a run over a DIFFERENT period inserts a new seq
-- (the check the async multi-algo runner's find_seq_id relies on).
CREATE UNIQUE INDEX IF NOT EXISTS uq_strategy_identity_natural
    ON strategy.strategy_identity
    (strategy_name, sec_type, code, start_date, end_date);

COMMENT ON COLUMN strategy.strategy_identity.start_date IS 'OHLC period start (df.date.min()) — the date the strategy is run FROM. Part of the natural business key (with end_date) so re-running over the same period is idempotent. Mirrors strategy_results.start_date but that is the OUTPUT (min exec_date); this is the INPUT period.';
COMMENT ON COLUMN strategy.strategy_identity.end_date IS 'OHLC period end (df.date.max()) — the date the strategy is run TO. NULL = open-ended (rare). Part of the natural business key with start_date.';

COMMENT ON COLUMN strategy.strategy_identity.fault_tolerance IS
    'Fault tolerance percentage (0-20) applied to decision-day OHLC. 0 = baseline (no stress). When >0, OHLC was perturbed on baseline decision dates by ft% of |delta_close| in BOTH directions (UP and DOWN). The algo re-ran on each stressed OHLC (same precomputed tech stats) and the stressed signal_confidences were stored on trade_decision.ft_stressed_conf_up / _down for comparison. Encoded in strategy_name as _ft{N} suffix.';


COMMENT ON TABLE  strategy.strategy_identity              IS 'One row per strategy execution on ONE code. Pure identity table — run results live on strategy_results (1:1).';
COMMENT ON COLUMN strategy.strategy_identity.seq_id       IS 'Surrogate primary key (IDENTITY). Identifies a single (strategy, code) run; also the PK/FK of the 1:1 strategy_results row.';
COMMENT ON COLUMN strategy.strategy_identity.strategy_name IS 'Strategy identifier, e.g. "singleton_trading".';
COMMENT ON COLUMN strategy.strategy_identity.seq_no       IS 'Run/sequence number within a strategy_name (1, 2, 3, ...). Multiple codes can share a seq_no within one --all run; they get distinct seq_ids but the same seq_no.';
COMMENT ON COLUMN strategy.strategy_identity.sec_type     IS 'Security universe: index / etf / stock.';
COMMENT ON COLUMN strategy.strategy_identity.code         IS 'Security code this run backtested (e.g. "000970", "159007.SZ"). One seq = one code.';
COMMENT ON COLUMN strategy.strategy_identity.params       IS 'Strategy parameters as JSONB (e.g. {"ma_short":5,"ma_long":60,"buy_notional":100000,"min_holding_period":7}).';
COMMENT ON COLUMN strategy.strategy_identity.status       IS 'Run lifecycle: running / completed / stopped / error.';
COMMENT ON COLUMN strategy.strategy_identity.created_at   IS 'Row creation timestamp (UTC).';

COMMENT ON COLUMN strategy.strategy_identity.is_active IS
    'Whether this run is the active one for its (strategy_name, sec_type, code). The UI loads active runs by default. When a new run is created, the old active run is deactivated and the new one becomes active. Default TRUE for new runs.';

-- (a) strategy_identity: look up runs by name/number, and per-code latest
CREATE INDEX IF NOT EXISTS idx_strategy_identity_name_no
    ON strategy.strategy_identity (strategy_name, seq_no);

CREATE INDEX IF NOT EXISTS idx_strategy_identity_type_code_no
    ON strategy.strategy_identity (sec_type, code, seq_no DESC);

-- Index for fast lookup of active runs per (strategy_name, sec_type, code).
CREATE INDEX IF NOT EXISTS idx_strategy_identity_active
    ON strategy.strategy_identity(strategy_name, sec_type, code)
    WHERE is_active = TRUE AND parent_seq_id IS NULL;
