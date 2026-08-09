-- ============================================================================
--  Trade Decision Sequences
--  Records each strategy execution (one backtest run on ONE code) and the
--  ordered trade decisions executed within it. Each strategy_seq row carries
--  the full capital budget for that single code, so trade_decision.cash_after
--  and strategy_seq.capital are on the SAME scale — Total Return =
--  (final_cash - capital) / capital is meaningful per-code (no division by
--  n_codes confusion).
--
--  Tables:
--    strategy.strategy_seq   — one row per (strategy, code) run
--    strategy.trade_decision — ordered trade decisions within a seq
--    strategy.v_trade_decision_full — convenience JOIN of the two
--
--  Usage: psql -d strategy -f strategy/01_trade_decision_seqs.sql
-- ============================================================================

-- ----------------------------------------------------------------------------
--  Schema + grants (mirrors analysis schema conventions defined in
--  database/sql/analysis/01_analysis_schema.sql)
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

-- Ensure postgres has full privileges on any existing objects
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA strategy TO postgres;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA strategy TO postgres;

-- Add strategy to the postgres search path (after stats, analysis, public)
ALTER ROLE postgres SET search_path TO stats, analysis, strategy, public;

-- ----------------------------------------------------------------------------
-- Table: strategy_seq
--   One row per strategy execution on ONE code. Carries total_buy_cost (the
--   accumulated cost of all BUY decisions in this run, computed AFTER the
--   backtest) — NOT a pre-set capital budget. There is no fixed capital; each
--   BUY deploys a fixed notional scaled by confidence, and position accumulates
--   freely (unlimited buys). SELLs close a fraction of the current position.
--   Total Return = final_cash / total_buy_cost (percentage return on total
--   invested), so it's directly comparable to realized_pnl.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS strategy.strategy_seq (
    seq_id                    BIGINT        GENERATED ALWAYS AS IDENTITY,
    strategy_name             TEXT          NOT NULL,
    seq_no                    INTEGER       NOT NULL DEFAULT 1,
    sec_type                  TEXT          NOT NULL DEFAULT 'index'
        CHECK (sec_type IN ('index', 'etf', 'stock')),
    code                      TEXT          NOT NULL,
    start_date                DATE          NOT NULL,
    end_date                  DATE,
    total_buy_cost            NUMERIC(24,4),
    currency                  TEXT          NOT NULL DEFAULT 'CNY',
    params                    JSONB         NOT NULL DEFAULT '{}'::jsonb,
    status                    TEXT          NOT NULL DEFAULT 'completed'
        CHECK (status IN ('running', 'completed', 'stopped', 'error')),
    created_at                TIMESTAMPTZ   NOT NULL DEFAULT now(),

    CONSTRAINT pk_strategy_seq PRIMARY KEY (seq_id),
    CONSTRAINT uq_strategy_seq_name_no_type_code
        UNIQUE (strategy_name, seq_no, sec_type, code),
    CONSTRAINT chk_strategy_seq_dates CHECK (end_date IS NULL OR end_date >= start_date),
    CONSTRAINT chk_strategy_seq_buy_cost CHECK (total_buy_cost IS NULL OR total_buy_cost > 0)
);

COMMENT ON TABLE  strategy.strategy_seq                  IS 'One row per strategy execution on ONE code. No fixed capital budget — total_buy_cost is the accumulated cost of all BUYs, computed after the backtest. Total Return = final_cash / total_buy_cost.';
COMMENT ON COLUMN strategy.strategy_seq.seq_id           IS 'Surrogate primary key (IDENTITY). Identifies a single (strategy, code) run.';
COMMENT ON COLUMN strategy.strategy_seq.strategy_name    IS 'Strategy identifier, e.g. "ma_spread_trading".';
COMMENT ON COLUMN strategy.strategy_seq.seq_no           IS 'Run/sequence number within a strategy_name (1, 2, 3, ...). Multiple codes can share a seq_no within one --all run; they get distinct seq_ids but the same seq_no.';
COMMENT ON COLUMN strategy.strategy_seq.sec_type         IS 'Security universe: index / etf / stock.';
COMMENT ON COLUMN strategy.strategy_seq.code            IS 'Security code this run backtested (e.g. "000970", "159007.SZ"). One seq = one code.';
COMMENT ON COLUMN strategy.strategy_seq.start_date       IS 'Inclusive run start date (first date the strategy may trade on).';
COMMENT ON COLUMN strategy.strategy_seq.end_date         IS 'Inclusive run end date (NULL = open-ended / still running).';
COMMENT ON COLUMN strategy.strategy_seq.total_buy_cost   IS 'Accumulated cost of all BUY decisions (gross_value + commission + fees) in this run. Computed AFTER the backtest. Replaces the old capital concept. Total Return = final_cash / total_buy_cost.';
COMMENT ON COLUMN strategy.strategy_seq.currency        IS 'Settlement currency of cash/price columns. Defaults to CNY.';
COMMENT ON COLUMN strategy.strategy_seq.params          IS 'Strategy parameters as JSONB (e.g. {"ma_short":5,"ma_long":60,"buy_notional":100000,"min_holding_period":7}).';
COMMENT ON COLUMN strategy.strategy_seq.status          IS 'Run lifecycle: running / completed / stopped / error.';
COMMENT ON COLUMN strategy.strategy_seq.created_at      IS 'Row creation timestamp (UTC).';

-- ----------------------------------------------------------------------------
-- Table: trade_decision
--   Ordered trade decisions within a strategy_seq (which is per-code, so no
--   code column here — it lives on strategy_seq). Simplified to the columns
--   actually consumed by the UI tooltip / decision table + portfolio
--   bookkeeping. Removed from the previous version: sec_type, code (now on
--   seq), filled_qty (always = qty), exec_time, order_type, limit_price,
--   avg_fill_price, slippage, net_value (derivable), unrealized_pnl,
--   min_holding_period (now in seq.params), status (always FILLED),
--   created_at, updated_at.
--
--   CHECK (position_after >= 0) enforces the long-only/no-shorting rule at
--   the DB level — a SELL can never drive the position below zero.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS strategy.trade_decision (
    seq_id                    BIGINT        NOT NULL,
    decision_no               INTEGER       NOT NULL,

    side                      TEXT          NOT NULL
        CHECK (side IN ('BUY', 'SELL')),
    qty                       NUMERIC(8,4)  NOT NULL CHECK (qty > 0 AND qty <= 100),

    signal_date               DATE          NOT NULL,
    exec_date                 DATE          NOT NULL,

    fill_price                NUMERIC(18,6) NOT NULL,

    -- Value & cost (kept for UI tooltip display; net_value is derivable)
    gross_value               NUMERIC(18,4) NOT NULL DEFAULT 0,
    commission                NUMERIC(18,4) NOT NULL DEFAULT 0,
    fees                      NUMERIC(18,4) NOT NULL DEFAULT 0,

    -- Portfolio state around this decision
    position_before           NUMERIC(24,4) NOT NULL DEFAULT 0,
    position_after            NUMERIC(24,4) NOT NULL DEFAULT 0
        CHECK (position_after >= 0),  -- long-only: SELL cannot exceed position
    cash_before               NUMERIC(24,4) NOT NULL DEFAULT 0,
    cash_after                NUMERIC(24,4) NOT NULL DEFAULT 0,

    -- P&L
    realized_pnl              NUMERIC(24,4) NOT NULL DEFAULT 0,

    -- Strategy signal context
    signal_value              NUMERIC(18,6),
    signal_reason              TEXT,

    CONSTRAINT pk_trade_decision PRIMARY KEY (seq_id, decision_no),
    CONSTRAINT fk_trade_decision_seq FOREIGN KEY (seq_id)
        REFERENCES strategy.strategy_seq(seq_id) ON DELETE CASCADE,
    CONSTRAINT chk_trade_decision_dates CHECK (exec_date >= signal_date)
);

COMMENT ON TABLE  strategy.trade_decision                 IS 'Ordered trade decisions within a strategy_seq (which is per-code). Simplified: only the columns actually used by the UI tooltip / decision table + portfolio bookkeeping. position_after >= 0 is enforced at the DB level (long-only / no shorting).';
COMMENT ON COLUMN strategy.trade_decision.seq_id          IS 'FK → strategy_seq.seq_id. Identifies the (strategy, code) run this decision belongs to.';
COMMENT ON COLUMN strategy.trade_decision.decision_no     IS '1-based ordinal of this decision within its seq (chronological). PK together with seq_id.';
COMMENT ON COLUMN strategy.trade_decision.side            IS 'Trade direction: BUY or SELL.';
COMMENT ON COLUMN strategy.trade_decision.qty             IS 'Confidence score in (0, 100]. BUY deploys (qty/100)*capital; SELL closes (qty/100)*current position (capped, never shorts).';
COMMENT ON COLUMN strategy.trade_decision.signal_date    IS 'Date the strategy signal fired (bar the decision was taken on).';
COMMENT ON COLUMN strategy.trade_decision.exec_date      IS 'Date the order was executed/filled. Usually signal_date + 1 trading day.';
COMMENT ON COLUMN strategy.trade_decision.fill_price     IS 'Execution (fill) price.';
COMMENT ON COLUMN strategy.trade_decision.gross_value     IS 'shares * fill_price (notional of the executed quantity).';
COMMENT ON COLUMN strategy.trade_decision.commission     IS 'Broker commission charged (>= 0).';
COMMENT ON COLUMN strategy.trade_decision.fees           IS 'Other fees/taxes (e.g. 印花税 stamp duty). >= 0.';
COMMENT ON COLUMN strategy.trade_decision.position_before IS 'Position (shares) held immediately before this decision.';
COMMENT ON COLUMN strategy.trade_decision.position_after  IS 'Position held immediately after. CHECK >= 0 enforces long-only (SELL cannot exceed position).';
COMMENT ON COLUMN strategy.trade_decision.cash_before     IS 'Cash balance immediately before this decision (in strategy_seq.capital units).';
COMMENT ON COLUMN strategy.trade_decision.cash_after      IS 'Cash balance immediately after: BUY → cash_before - (gross + commission + fees); SELL → cash_before + (gross - commission - fees).';
COMMENT ON COLUMN strategy.trade_decision.realized_pnl   IS 'Realized P&L booked on SELL (proceeds minus cost basis of the closed lots). 0 for BUY.';
COMMENT ON COLUMN strategy.trade_decision.signal_value   IS 'Numeric value of the triggering signal (e.g. MA spread).';
COMMENT ON COLUMN strategy.trade_decision.signal_reason  IS 'Human-readable reason the signal fired (e.g. "MA5 crossed above MA60 by 2.1%").';

-- ----------------------------------------------------------------------------
-- Indexes
-- ----------------------------------------------------------------------------
-- (a) strategy_seq: look up runs by name/number, and per-code latest
CREATE INDEX IF NOT EXISTS idx_strategy_seq_name_no
    ON strategy.strategy_seq (strategy_name, seq_no);

CREATE INDEX IF NOT EXISTS idx_strategy_seq_type_code_no
    ON strategy.strategy_seq (sec_type, code, seq_no DESC);

-- (b) trade_decision: chronological lookup within a seq
CREATE INDEX IF NOT EXISTS idx_trade_decision_seq_signal_date
    ON strategy.trade_decision (seq_id, signal_date);

-- ----------------------------------------------------------------------------
-- View: v_trade_decision_full
--   Convenience JOIN of strategy_seq + trade_decision so readers get the
--   run context (strategy_name, capital, params, code) alongside each
--   decision without a manual JOIN.
-- ----------------------------------------------------------------------------
DROP VIEW IF EXISTS strategy.v_trade_decision_full;
CREATE OR REPLACE VIEW strategy.v_trade_decision_full AS
SELECT
    d.seq_id,
    s.strategy_name,
    s.seq_no,
    s.sec_type,
    s.code,
    s.total_buy_cost,
    s.currency,
    s.params            AS seq_params,
    s.start_date        AS seq_start_date,
    s.end_date          AS seq_end_date,
    s.status            AS seq_status,
    d.decision_no,
    d.side,
    d.qty,
    d.signal_date,
    d.exec_date,
    d.fill_price,
    d.gross_value,
    d.commission,
    d.fees,
    d.position_before,
    d.position_after,
    d.cash_before,
    d.cash_after,
    d.realized_pnl,
    d.signal_value,
    d.signal_reason
FROM strategy.trade_decision d
JOIN strategy.strategy_seq s ON s.seq_id = d.seq_id;

COMMENT ON VIEW strategy.v_trade_decision_full IS 'Convenience JOIN of strategy_seq + trade_decision: run context (incl. code, capital) alongside each decision.';

-- ============================================================================
--  Internal Risk Analytics
--  Computes strategy risk metrics from the trade_decision history.
--
--  Two tables:
--    strategy.strategy_risk_seq    — one row per (seq_id, code) with run-level
--                                    risk metrics: chronological concentration
--                                    of gains/losses, exponential risk score,
--                                    top gain/loss trade references.
--    strategy.strategy_risk_period — per-period (year / season / month)
--                                    aggregations of gains/losses with the top
--                                    trades in each period, so the user can see
--                                    WHERE in time the strategy made/lost money.
--
--  Risk philosophy (chronological concentration):
--    If most gains/losses are concentrated in a short period, risk INCREASES
--    EXPONENTIALLY (a clustered drawdown is far more dangerous than a spread-
--    out one of equal magnitude, because it implies regime-dependent behavior
--    and a higher probability of ruin during that regime).
--    If gains/losses are spread evenly across time, risk DROPS (the strategy
--    performs consistently across regimes — lower regime dependence).
--
--  The concentration_ratio ∈ [0,1] measures the share of total |P&L| that
--  falls in the worst 30-day rolling window. risk_score maps it via an
--    exponential curve: risk_score = concentration_ratio^2 * |max_drawdown|.
-- ============================================================================
CREATE TABLE IF NOT EXISTS strategy.strategy_risk_seq (
    seq_id                        BIGINT        NOT NULL,
    code                          TEXT          NOT NULL,

    -- P&L summary (from SELL decisions)
    total_realized_pnl            NUMERIC(24,4) NOT NULL DEFAULT 0,
    total_abs_pnl                 NUMERIC(24,4) NOT NULL DEFAULT 0,  -- sum |realized_pnl|
    n_sells                       INTEGER       NOT NULL DEFAULT 0,
    n_buys                        INTEGER       NOT NULL DEFAULT 0,

    -- Top trade references (denormalized for fast UI display)
    top_gain_pnl                  NUMERIC(24,4),
    top_gain_exec_date            DATE,
    top_gain_signal_reason        TEXT,
    top_loss_pnl                  NUMERIC(24,4),
    top_loss_exec_date            DATE,
    top_loss_signal_reason        TEXT,

    -- Chronological concentration
    --   max_30d_abs_pnl: the largest 30-day rolling sum of |realized_pnl|
    --   concentration_ratio = max_30d_abs_pnl / total_abs_pnl  (∈ [0,1])
    --   1.0 = all P&L crammed into a single 30-day window (max risk)
    --   ~0  = perfectly spread across time (min risk)
    max_30d_abs_pnl               NUMERIC(24,4),
    concentration_ratio           NUMERIC(10,6),  -- [0,1]
    concentration_window_start    DATE,           -- start of the worst 30d window
    concentration_window_end      DATE,           -- end of the worst 30d window

    -- Exponential risk score
    --   risk_score = concentration_ratio^2 * ABS(max_drawdown)
    --   The squared exponent makes clustered P&L exponentially more risky.
    --   max_drawdown is the worst peak-to-trough decline in cumulative P&L.
    max_drawdown                  NUMERIC(24,4),  -- worst peak-to-trough (<= 0)
    risk_score                    NUMERIC(24,4),  -- concentration_ratio^2 * |max_drawdown|

    -- Price-based drawdowns (worst unrealized peak-to-trough decline of the
    --   security's CLOSE price, as a signed fractional ratio <= 0).
    --   deepest_drop_since_unzero_pos: worst close-price drawdown observed
    --     during any maximal span where position > 0 (unzero holding period).
    --     Captures the worst paper-loss endured while holding.
    --   deepest_drop_since_last_buy: worst close-price drawdown observed from
    --     a BUY entry (seed peak = fill_price) until the next decision.
    --     Captures the maximum adverse excursion following an entry.
    --   peak_date / trough_date pinpoint where each drop occurred (for UI).
    deepest_drop_since_unzero_pos         NUMERIC(10,6),  -- fractional ratio (<= 0)
    deepest_drop_since_unzero_pos_peak_date    DATE,
    deepest_drop_since_unzero_pos_trough_date  DATE,
    deepest_drop_since_last_buy           NUMERIC(10,6),  -- fractional ratio (<= 0)
    deepest_drop_since_last_buy_peak_date      DATE,
    deepest_drop_since_last_buy_trough_date    DATE,

    -- Risk grade (derived from risk_score / total_abs_pnl ratio)
    --   ratio = risk_score / NULLIF(total_abs_pnl, 0)
    --   < 0.10 = LOW, 0.10–0.25 = MODERATE, 0.25–0.50 = ELEVATED, > 0.50 = HIGH
    risk_grade                    TEXT
        CHECK (risk_grade IN ('LOW', 'MODERATE', 'ELEVATED', 'HIGH')),

    computed_at                   TIMESTAMPTZ   NOT NULL DEFAULT now(),

    CONSTRAINT pk_strategy_risk_seq PRIMARY KEY (seq_id, code),
    CONSTRAINT fk_strategy_risk_seq_seq FOREIGN KEY (seq_id)
        REFERENCES strategy.strategy_seq(seq_id) ON DELETE CASCADE,
    CONSTRAINT chk_risk_concentration CHECK (
        concentration_ratio IS NULL OR
        (concentration_ratio >= 0 AND concentration_ratio <= 1)
    ),
    CONSTRAINT chk_risk_drawdown CHECK (
        max_drawdown IS NULL OR max_drawdown <= 0
    ),
    -- Price-based drops are signed fractional ratios (<= 0); 0 = no drop.
    CONSTRAINT chk_risk_drop_unzero CHECK (
        deepest_drop_since_unzero_pos IS NULL OR
        deepest_drop_since_unzero_pos <= 0
    ),
    CONSTRAINT chk_risk_drop_last_buy CHECK (
        deepest_drop_since_last_buy IS NULL OR
        deepest_drop_since_last_buy <= 0
    )
);

COMMENT ON TABLE  strategy.strategy_risk_seq                  IS 'Per-(seq, code) risk metrics: chronological P&L concentration, exponential risk score, top gain/loss trades.';
COMMENT ON COLUMN strategy.strategy_risk_seq.seq_id           IS 'FK → strategy_seq.seq_id.';
COMMENT ON COLUMN strategy.strategy_risk_seq.code             IS 'Security code this risk row pertains to (denormalized from strategy_seq.code for fast UI display).';
COMMENT ON COLUMN strategy.strategy_risk_seq.total_realized_pnl IS 'Sum of realized_pnl across all SELL decisions for this (seq, code).';
COMMENT ON COLUMN strategy.strategy_risk_seq.total_abs_pnl    IS 'Sum of ABS(realized_pnl) across all SELL decisions — the total P&L turnover.';
COMMENT ON COLUMN strategy.strategy_risk_seq.top_gain_pnl     IS 'The largest single-trade gain (max realized_pnl among SELLs).';
COMMENT ON COLUMN strategy.strategy_risk_seq.top_loss_pnl     IS 'The largest single-trade loss (min realized_pnl among SELLs).';
COMMENT ON COLUMN strategy.strategy_risk_seq.max_30d_abs_pnl  IS 'The largest 30-day rolling sum of |realized_pnl| — peak P&L concentration window.';
COMMENT ON COLUMN strategy.strategy_risk_seq.concentration_ratio IS 'max_30d_abs_pnl / total_abs_pnl. 1.0 = all P&L in one 30-day window (max risk); ~0 = evenly spread (min risk).';
COMMENT ON COLUMN strategy.strategy_risk_seq.concentration_window_start IS 'Start date of the worst (most concentrated) 30-day P&L window.';
COMMENT ON COLUMN strategy.strategy_risk_seq.concentration_window_end   IS 'End date of the worst 30-day P&L window.';
COMMENT ON COLUMN strategy.strategy_risk_seq.max_drawdown     IS 'Worst peak-to-trough decline in cumulative realized P&L (always <= 0).';
COMMENT ON COLUMN strategy.strategy_risk_seq.risk_score       IS 'Exponential risk score = concentration_ratio^2 * ABS(max_drawdown). Higher = more dangerous (clustered drawdowns compound risk).';
COMMENT ON COLUMN strategy.strategy_risk_seq.risk_grade       IS 'LOW / MODERATE / ELEVATED / HIGH — derived from risk_score / total_abs_pnl ratio.';
COMMENT ON COLUMN strategy.strategy_risk_seq.deepest_drop_since_unzero_pos IS 'Worst close-price peak-to-trough drawdown (signed fractional ratio, <= 0) observed during any maximal span where position > 0. Captures the worst paper-loss endured while holding. 0 = price only rose while holding.';
COMMENT ON COLUMN strategy.strategy_risk_seq.deepest_drop_since_unzero_pos_peak_date   IS 'Biz date of the peak (running max close) from which the worst unzero-position drop was measured.';
COMMENT ON COLUMN strategy.strategy_risk_seq.deepest_drop_since_unzero_pos_trough_date IS 'Biz date of the trough (lowest close) reached in the worst unzero-position drop.';
COMMENT ON COLUMN strategy.strategy_risk_seq.deepest_drop_since_last_buy  IS 'Worst close-price peak-to-trough drawdown (signed fractional ratio, <= 0) from a BUY entry (seed peak = fill_price) until the next decision. Maximum adverse excursion following an entry. 0 = price never fell below the entry after a buy.';
COMMENT ON COLUMN strategy.strategy_risk_seq.deepest_drop_since_last_buy_peak_date     IS 'Biz date of the peak (running max close, seeded by the BUY fill_price) from which the worst since-last-buy drop was measured.';
COMMENT ON COLUMN strategy.strategy_risk_seq.deepest_drop_since_last_buy_trough_date   IS 'Biz date of the trough (lowest close) reached in the worst since-last-buy drop.';
COMMENT ON COLUMN strategy.strategy_risk_seq.computed_at      IS 'Timestamp this risk row was computed (UTC).';

-- Migrate: add the price-based drawdown columns to pre-existing installs
-- (CREATE TABLE IF NOT EXISTS does not retro-fit columns to an already-
-- existing table). Each is either a signed fractional ratio (<= 0) or a DATE.
ALTER TABLE strategy.strategy_risk_seq ADD COLUMN IF NOT EXISTS deepest_drop_since_unzero_pos           NUMERIC(10,6);
ALTER TABLE strategy.strategy_risk_seq ADD COLUMN IF NOT EXISTS deepest_drop_since_unzero_pos_peak_date    DATE;
ALTER TABLE strategy.strategy_risk_seq ADD COLUMN IF NOT EXISTS deepest_drop_since_unzero_pos_trough_date  DATE;
ALTER TABLE strategy.strategy_risk_seq ADD COLUMN IF NOT EXISTS deepest_drop_since_last_buy             NUMERIC(10,6);
ALTER TABLE strategy.strategy_risk_seq ADD COLUMN IF NOT EXISTS deepest_drop_since_last_buy_peak_date      DATE;
ALTER TABLE strategy.strategy_risk_seq ADD COLUMN IF NOT EXISTS deepest_drop_since_last_buy_trough_date    DATE;
-- Add the CHECK constraints for the new columns (idempotent — wrapped in
-- DO block so re-running on installs that already have them is a no-op).
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'chk_risk_drop_unzero'
    ) THEN
        ALTER TABLE strategy.strategy_risk_seq
            ADD CONSTRAINT chk_risk_drop_unzero CHECK (
                deepest_drop_since_unzero_pos IS NULL OR
                deepest_drop_since_unzero_pos <= 0
            );
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'chk_risk_drop_last_buy'
    ) THEN
        ALTER TABLE strategy.strategy_risk_seq
            ADD CONSTRAINT chk_risk_drop_last_buy CHECK (
                deepest_drop_since_last_buy IS NULL OR
                deepest_drop_since_last_buy <= 0
            );
    END IF;
END $$;

-- ----------------------------------------------------------------------------
-- Table: strategy_risk_period
--   Per-period gain/loss aggregations. One row per (seq_id, code, period_type,
--   period_value). period_type is 'year' / 'season' / 'month'.
--
--   For each period: total realized P&L, n_trades, top gain/loss trade in
--   that period, and the period's share of the run's total |P&L| (so the UI
--   can flag periods that dominate the strategy's risk).
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS strategy.strategy_risk_period (
    seq_id                        BIGINT        NOT NULL,
    code                          TEXT          NOT NULL,
    period_type                   TEXT          NOT NULL
        CHECK (period_type IN ('year', 'season', 'month')),
    -- period_value encoding:
    --   year   = 'YYYY'               e.g. '2024'
    --   season = 'YYYY-Qn'            e.g. '2024-Q1' (Q1=Jan-Mar, ... Q4=Oct-Dec)
    --   month  = 'YYYY-MM'            e.g. '2024-08'
    period_value                  TEXT          NOT NULL,

    n_sells                       INTEGER       NOT NULL DEFAULT 0,
    n_buys                        INTEGER       NOT NULL DEFAULT 0,
    realized_pnl                  NUMERIC(24,4) NOT NULL DEFAULT 0,
    abs_pnl                       NUMERIC(24,4) NOT NULL DEFAULT 0,
    -- share of the run's total |P&L| that this period contributed (∈ [0,1])
    --   1.0 = this period accounts for all P&L activity (max concentration)
    period_share                  NUMERIC(10,6),

    -- Top trade in this period
    top_gain_pnl                  NUMERIC(24,4),
    top_gain_exec_date            DATE,
    top_loss_pnl                  NUMERIC(24,4),
    top_loss_exec_date            DATE,

    -- Is this period a "concentration hotspot"?
    --   A period is flagged if period_share >= 0.25 (quarter of all activity)
    --   OR its realized_pnl sign differs from the run's total (a counter-trend
    --   period that hidden risk).
    is_concentration_hotspot      BOOLEAN       NOT NULL DEFAULT FALSE,
    is_counter_trend              BOOLEAN       NOT NULL DEFAULT FALSE,

    computed_at                   TIMESTAMPTZ   NOT NULL DEFAULT now(),

    CONSTRAINT pk_strategy_risk_period PRIMARY KEY (seq_id, code, period_type, period_value),
    CONSTRAINT fk_strategy_risk_period_seq FOREIGN KEY (seq_id)
        REFERENCES strategy.strategy_seq(seq_id) ON DELETE CASCADE,
    CONSTRAINT chk_risk_period_share CHECK (
        period_share IS NULL OR (period_share >= 0 AND period_share <= 1)
    )
);

COMMENT ON TABLE  strategy.strategy_risk_period                  IS 'Per-period (year/season/month) gain/loss aggregations with top trades and concentration flags.';
COMMENT ON COLUMN strategy.strategy_risk_period.seq_id           IS 'FK → strategy_seq.seq_id.';
COMMENT ON COLUMN strategy.strategy_risk_period.code             IS 'Security code this period row pertains to.';
COMMENT ON COLUMN strategy.strategy_risk_period.period_type      IS 'year / season / month.';
COMMENT ON COLUMN strategy.strategy_risk_period.period_value     IS 'Period label: YYYY (year), YYYY-Qn (season), YYYY-MM (month).';
COMMENT ON COLUMN strategy.strategy_risk_period.realized_pnl     IS 'Sum of realized_pnl across SELLs in this period.';
COMMENT ON COLUMN strategy.strategy_risk_period.abs_pnl          IS 'Sum of |realized_pnl| across SELLs in this period.';
COMMENT ON COLUMN strategy.strategy_risk_period.period_share     IS 'abs_pnl / total_abs_pnl for the run. High share = concentrated activity in this period.';
COMMENT ON COLUMN strategy.strategy_risk_period.top_gain_pnl     IS 'Largest single-trade gain within this period.';
COMMENT ON COLUMN strategy.strategy_risk_period.top_loss_pnl     IS 'Largest single-trade loss within this period.';
COMMENT ON COLUMN strategy.strategy_risk_period.is_concentration_hotspot IS 'TRUE if period_share >= 0.25 — this period accounts for a quarter+ of all P&L activity.';
COMMENT ON COLUMN strategy.strategy_risk_period.is_counter_trend IS 'TRUE if this period''s realized_pnl sign differs from the run''s total (hidden counter-trend risk).';

-- Indexes
CREATE INDEX IF NOT EXISTS idx_strategy_risk_seq_code
    ON strategy.strategy_risk_seq (code);

CREATE INDEX IF NOT EXISTS idx_strategy_risk_period_seq_code_type
    ON strategy.strategy_risk_period (seq_id, code, period_type, period_value);

CREATE INDEX IF NOT EXISTS idx_strategy_risk_period_hotspot
    ON strategy.strategy_risk_period (seq_id, code)
    WHERE is_concentration_hotspot = TRUE;

-- ----------------------------------------------------------------------------
-- View: v_strategy_risk_full
--   Convenience JOIN of strategy_seq + strategy_risk_seq so readers get the
--   run context (strategy_name, params) alongside the risk metrics.
-- ----------------------------------------------------------------------------
DROP VIEW IF EXISTS strategy.v_strategy_risk_full;
CREATE OR REPLACE VIEW strategy.v_strategy_risk_full AS
SELECT
    r.seq_id,
    s.strategy_name,
    s.seq_no,
    s.sec_type,
    s.code,
    s.total_buy_cost,
    s.params                 AS seq_params,
    r.total_realized_pnl,
    r.total_abs_pnl,
    r.n_sells,
    r.n_buys,
    r.top_gain_pnl,
    r.top_gain_exec_date,
    r.top_gain_signal_reason,
    r.top_loss_pnl,
    r.top_loss_exec_date,
    r.top_loss_signal_reason,
    r.max_30d_abs_pnl,
    r.concentration_ratio,
    r.concentration_window_start,
    r.concentration_window_end,
    r.max_drawdown,
    r.risk_score,
    r.risk_grade,
    r.deepest_drop_since_unzero_pos,
    r.deepest_drop_since_unzero_pos_peak_date,
    r.deepest_drop_since_unzero_pos_trough_date,
    r.deepest_drop_since_last_buy,
    r.deepest_drop_since_last_buy_peak_date,
    r.deepest_drop_since_last_buy_trough_date,
    r.computed_at
FROM strategy.strategy_risk_seq r
JOIN strategy.strategy_seq s ON s.seq_id = r.seq_id;

COMMENT ON VIEW strategy.v_strategy_risk_full IS 'Convenience JOIN of strategy_seq + strategy_risk_seq: run context alongside risk metrics.';
