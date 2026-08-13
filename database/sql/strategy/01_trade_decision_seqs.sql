-- ============================================================================
--  Trade Decision Sequences
--  Records each strategy execution (one backtest run on ONE code) and the
--  ordered trade decisions executed within it.
--
--  Tables:
--    strategy.strategy_identity         — one row per (strategy, code) run (IDENTITY)
--    strategy.strategy_results     — 1:1 with strategy_identity: run RESULTS (dates,
--                                    total_buy_cost, first-buy anchor, P&L
--                                    summary moved here from strategy_identity /
--                                    strategy_risks)
--    strategy.trade_decision       — ordered decisions; carries
--                                    normalized_fill_price (base = 100 at the
--                                    first BUY fill)
--    strategy.strategy_daily       — daily portfolio state (one row per trading
--                                    day); unrealized_pnl = P&L if all remaining
--                                    position sold at the day's close
--    strategy.v_trade_decision_full — convenience JOIN of seq + info + decision
--
--  Normalization:
--    strategy_results.first_buy_fill_price is the anchor. Each trade_decision row
--    carries normalized_fill_price = fill_price / first_buy_fill_price * 100,
--    so the first BUY = 100 and every later fill reads as a % gain/loss from
--    the entry (105.0 = +5%, 94.1 = -5.9%). The UI also rebases the OHLC/MA
--    chart series off this same anchor.
--
--  No fixed capital: each BUY deploys (confidence/100) * buy_notional; cash
--  starts at 0 (goes negative on BUY = borrowing). MONEY metrics (position,
--  cash, realized_pnl, total_buy_cost) use shares = total_qty / 100 so they
--  are on a scale comparable to total_qty at the entry anchor (norm=100).
--  total_buy_cost = peak capital deployed = (max(total_qty_after)/100) ×
--  normalized_mean_buy_price at that decision. Computed AFTER the backtest
--  and stored on strategy_results. Total Return = final_cash / total_buy_cost.
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
-- Table: strategy_identity
--   One row per strategy execution on ONE code. PURE IDENTITY table — run
--   RESULTS (dates, total_buy_cost, first-buy anchor, P&L summary) live on
--   strategy_results (1:1). Splitting identity from results keeps strategy_identity
--   stable across recomputes and concentrates display fields in one place.
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
    created_at            TIMESTAMPTZ   NOT NULL DEFAULT now(),

    CONSTRAINT pk_strategy_identity PRIMARY KEY (seq_id),
    -- Natural business key (PK/FK-aligned uniqueness): one seq per
    -- (strategy_name, sec_type, code, period, scenario). seq_no is a
    -- display counter only — NOT part of uniqueness — so re-running an
    -- algo over the same period is idempotent (skip) and a new period
    -- just inserts a new seq.
    CONSTRAINT uq_strategy_identity_natural
        UNIQUE (strategy_name, sec_type, code, start_date, end_date)
);

-- Idempotent migration: add parent_seq_id + scenario for forecast child seqs.
-- Each forecast scenario (mir_255d_std_scale, flip_255d_std_scale, ...) gets its own child seq that
-- carries a full copy of the parent's actual decisions + that scenario's
-- forecast sells, enabling per-scenario risk + return + decision table.
ALTER TABLE strategy.strategy_identity
    ADD COLUMN IF NOT EXISTS parent_seq_id BIGINT;

ALTER TABLE strategy.strategy_identity
    ADD COLUMN IF NOT EXISTS scenario TEXT;

ALTER TABLE strategy.strategy_identity
    DROP CONSTRAINT IF EXISTS fk_strategy_identity_parent;

ALTER TABLE strategy.strategy_identity
    ADD CONSTRAINT fk_strategy_identity_parent
        FOREIGN KEY (parent_seq_id)
        REFERENCES strategy.strategy_identity(seq_id)
        ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS idx_strategy_identity_parent
    ON strategy.strategy_identity(parent_seq_id)
    WHERE parent_seq_id IS NOT NULL;

-- Idempotent migration: add start_date / end_date to strategy_identity for
-- existing DBs (the columns are already in the CREATE TABLE above for fresh
-- DBs). start_date/end_date are the OHLC period the strategy is run over
-- (input); they are part of the natural business key so the "skip if already
-- found" check maps 1:1 to the unique constraint.
ALTER TABLE strategy.strategy_identity
    ADD COLUMN IF NOT EXISTS start_date DATE;
ALTER TABLE strategy.strategy_identity
    ADD COLUMN IF NOT EXISTS end_date DATE;

-- Backfill start_date/end_date from strategy_results for existing seqs
-- (strategy_results already carries the run period as min/max exec_date).
-- The first_buy_date is used as the start_date fallback (defensive: every
-- seq with decisions has a first BUY). Rows with neither stay NULL and are
-- dropped by the NOT NULL step below (they are degenerate/empty runs).
UPDATE strategy.strategy_identity s
SET start_date = COALESCE(r.start_date, r.first_buy_date),
    end_date   = r.end_date
FROM strategy.strategy_results r
WHERE r.seq_id = s.seq_id
  AND s.start_date IS NULL;

-- Drop any degenerate rows that still have NULL start_date (no results row
-- and no first_buy_date) — they cannot satisfy the NOT NULL constraint and
-- carry no useful data.
DELETE FROM strategy.strategy_identity
WHERE start_date IS NULL;

ALTER TABLE strategy.strategy_identity
    ALTER COLUMN start_date SET NOT NULL;

-- Replace the (strategy_name, seq_no, sec_type, code) unique constraint with
-- the NATURAL business key: (strategy_name, sec_type, code, start_date,
-- end_date, COALESCE(scenario, '')). seq_no is now a display counter only.
-- This makes the "skip if already found in strategy_identity" check (used
-- by the async multi-algo runner) align 1:1 with the unique constraint, and
-- lets forecast child seqs (non-NULL scenario) coexist with the parent.
ALTER TABLE strategy.strategy_identity
    DROP CONSTRAINT IF EXISTS uq_strategy_identity_name_no_type_code;
ALTER TABLE strategy.strategy_identity
    DROP CONSTRAINT IF EXISTS uq_strategy_identity_natural;

CREATE UNIQUE INDEX IF NOT EXISTS uq_strategy_identity_natural
    ON strategy.strategy_identity
    (strategy_name, sec_type, code, start_date, end_date, COALESCE(scenario, ''));

COMMENT ON COLUMN strategy.strategy_identity.start_date IS 'OHLC period start (df.date.min()) — the date the strategy is run FROM. Part of the natural business key (with end_date) so re-running over the same period is idempotent. Mirrors strategy_results.start_date but that is the OUTPUT (min exec_date); this is the INPUT period.';
COMMENT ON COLUMN strategy.strategy_identity.end_date IS 'OHLC period end (df.date.max()) — the date the strategy is run TO. NULL = open-ended (rare). Part of the natural business key with start_date.';

COMMENT ON COLUMN strategy.strategy_identity.parent_seq_id IS 'NULL for actual backtest seqs. For forecast child seqs: points to the parent actual backtest seq. Child seqs carry a full copy of actual decisions + scenario-specific forecast sells.';
COMMENT ON COLUMN strategy.strategy_identity.scenario IS 'NULL for actual backtest seqs. For forecast child seqs: the scenario name (mir_255d_std_scale, flip_255d_std_scale, mir_255d_std_half_scale, flip_255d_std_half_scale, mir_20d_std_scale, flip_20d_std_scale, rand, rand_opp).';

COMMENT ON TABLE  strategy.strategy_identity              IS 'One row per strategy execution on ONE code. Pure identity table — run results live on strategy_results (1:1).';
COMMENT ON COLUMN strategy.strategy_identity.seq_id       IS 'Surrogate primary key (IDENTITY). Identifies a single (strategy, code) run; also the PK/FK of the 1:1 strategy_results row.';
COMMENT ON COLUMN strategy.strategy_identity.strategy_name IS 'Strategy identifier, e.g. "singleton_trading".';
COMMENT ON COLUMN strategy.strategy_identity.seq_no       IS 'Run/sequence number within a strategy_name (1, 2, 3, ...). Multiple codes can share a seq_no within one --all run; they get distinct seq_ids but the same seq_no.';
COMMENT ON COLUMN strategy.strategy_identity.sec_type     IS 'Security universe: index / etf / stock.';
COMMENT ON COLUMN strategy.strategy_identity.code         IS 'Security code this run backtested (e.g. "000970", "159007.SZ"). One seq = one code.';
COMMENT ON COLUMN strategy.strategy_identity.params       IS 'Strategy parameters as JSONB (e.g. {"ma_short":5,"ma_long":60,"buy_notional":100000,"min_holding_period":7}).';
COMMENT ON COLUMN strategy.strategy_identity.status       IS 'Run lifecycle: running / completed / stopped / error.';
COMMENT ON COLUMN strategy.strategy_identity.created_at   IS 'Row creation timestamp (UTC).';

-- ----------------------------------------------------------------------------
-- Table: strategy_results
--   1:1 with strategy_identity (seq_id is both PK and FK). Holds the run RESULTS:
--     - dates (start/end) + total_buy_cost + currency (moved from strategy_identity)
--     - first_buy_date + first_buy_fill_price: the normalization anchor. Each
--       trade_decision.normalized_fill_price = fill_price / first_buy_fill_price
--       * 100, so the first BUY reads as 100 and later fills as % change.
--     - P&L summary (total_realized_pnl, total_abs_pnl, n_sells, n_buys) moved
--       here from strategy_risks so all displayable result fields live in
--       one table; strategy_risks now keeps only risk-specific metrics.
--   Written by the backtest runner (strategy._common.runner) right after
--   strategy_identity is inserted, from the decisions list.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS strategy.strategy_results (
    seq_id                BIGINT        NOT NULL,
    sec_type              TEXT          NOT NULL DEFAULT 'index'
        CHECK (sec_type IN ('index', 'etf', 'stock')),
    code                  TEXT          NOT NULL,

    start_date            DATE          NOT NULL,
    end_date              DATE,
    total_buy_cost        NUMERIC(24,4),
    currency              TEXT          NOT NULL DEFAULT 'CNY',

    -- Normalization anchor: the first BUY fill. NULL only if the run made no
    -- BUY (degenerate — every run with decisions has ≥1 BUY by construction).
    first_buy_date        DATE,
    first_buy_fill_price  NUMERIC(18,6),

    -- P&L summary (from SELL decisions) — moved from strategy_risks
    total_realized_pnl    NUMERIC(24,4) NOT NULL DEFAULT 0,
    total_abs_pnl         NUMERIC(24,4) NOT NULL DEFAULT 0,  -- sum |realized_pnl|
    n_sells               INTEGER       NOT NULL DEFAULT 0,
    n_buys                INTEGER       NOT NULL DEFAULT 0,

    CONSTRAINT pk_strategy_results PRIMARY KEY (seq_id),
    CONSTRAINT fk_strategy_results_seq
        FOREIGN KEY (seq_id)
        REFERENCES strategy.strategy_identity (seq_id)
        ON DELETE CASCADE ON UPDATE CASCADE,
    CONSTRAINT chk_strategy_results_dates
        CHECK (end_date IS NULL OR end_date >= start_date),
    CONSTRAINT chk_strategy_results_buy_cost
        CHECK (total_buy_cost IS NULL OR total_buy_cost > 0),
    CONSTRAINT chk_strategy_results_first_buy
        CHECK (
            (first_buy_date IS NULL AND first_buy_fill_price IS NULL) OR
            (first_buy_date IS NOT NULL AND first_buy_fill_price IS NOT NULL
             AND first_buy_fill_price > 0)
        )
);

COMMENT ON TABLE  strategy.strategy_results                  IS '1:1 with strategy_identity. Holds run RESULTS: dates, total_buy_cost, the first-buy normalization anchor, and P&L summary (moved from strategy_identity / strategy_risks).';
COMMENT ON COLUMN strategy.strategy_results.seq_id           IS 'PK + FK → strategy_identity.seq_id (1:1).';
COMMENT ON COLUMN strategy.strategy_results.sec_type         IS 'Security universe (denormalized from strategy_identity for fast UI display).';
COMMENT ON COLUMN strategy.strategy_results.code             IS 'Security code (denormalized from strategy_identity).';
COMMENT ON COLUMN strategy.strategy_results.start_date       IS 'Inclusive run start date = min(decisions.exec_date).';
COMMENT ON COLUMN strategy.strategy_results.end_date         IS 'Inclusive run end date = max(decisions.exec_date). NULL = open-ended.';
COMMENT ON COLUMN strategy.strategy_results.total_buy_cost   IS 'Peak capital deployed = (max(total_qty_after across all decisions) / 100) × normalized_mean_buy_price at that decision. Money uses shares = total_qty/100. Total Return = final_cash / total_buy_cost.';
COMMENT ON COLUMN strategy.strategy_results.currency         IS 'Settlement currency of cash/price columns. Defaults to CNY.';
COMMENT ON COLUMN strategy.strategy_results.first_buy_date   IS 'exec_date of the FIRST BUY decision — the normalization anchor date. NULL only if no BUY occurred.';
COMMENT ON COLUMN strategy.strategy_results.first_buy_fill_price IS 'fill_price of the FIRST BUY decision. trade_decision.normalized_fill_price = fill_price / this * 100, so the first BUY reads as 100. NULL only if no BUY occurred.';
COMMENT ON COLUMN strategy.strategy_results.total_realized_pnl IS 'Sum of realized_pnl across all SELL decisions for this run.';
COMMENT ON COLUMN strategy.strategy_results.total_abs_pnl    IS 'Sum of ABS(realized_pnl) across all SELL decisions — total P&L turnover.';
COMMENT ON COLUMN strategy.strategy_results.n_sells          IS 'Count of SELL decisions in this run.';
COMMENT ON COLUMN strategy.strategy_results.n_buys           IS 'Count of BUY decisions in this run.';

-- ----------------------------------------------------------------------------
-- Table: trade_decision
--   Ordered trade decisions within a strategy_identity (which is per-code, so no
--   code column here — it lives on strategy_identity/strategy_results). Simplified to
--   the columns actually consumed by the UI tooltip / decision table +
--   portfolio bookkeeping.
--
--   normalized_fill_price: fill_price rebased to 100 at the first BUY fill
--     (= fill_price / strategy_results.first_buy_fill_price * 100). First BUY =
--     100; later fills read as % change from entry (105 = +5%, 94 = -6%).
--
--   `commission` was removed: per-trade costs (broker commission + stamp duty
--     + other fees) are folded into a single `fees` column. The backtest still
--     computes commission_rate vs stamp_duty separately for realized_pnl; only
--     the stored column is combined.
--
--   CHECK (position_after >= 0) enforces the long-only/no-shorting rule at
--   the DB level — a SELL can never drive the position below zero.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS strategy.trade_decision (
    seq_id                    BIGINT        NOT NULL,
    decision_no               INTEGER       NOT NULL,

    side                      TEXT          NOT NULL
        CHECK (side IN ('BUY', 'SELL')),
    qty                       NUMERIC(12,4) NOT NULL CHECK (qty > 0),

    exec_date                 DATE          NOT NULL,

    fill_price                NUMERIC(18,6) NOT NULL,
    normalized_fill_price     NUMERIC(18,6) NOT NULL,
    normalized_mean_buy_price NUMERIC(18,6) NOT NULL,

    -- Portfolio state around this decision (ALL money in normalized units).
    -- total_qty is the cumulative quantity (sum of qty for BUYs minus
    -- qty_sold for SELLs, in confidence/qty units NOT /100). MONEY metrics
    -- (position, cash, realized_pnl) use shares = total_qty / 100, so they
    -- are on a scale comparable to total_qty at the entry anchor (norm=100):
    --   position = (total_qty / 100) × normalized_fill_price
    --   cash: BUY subtracts (qty/100)*norm_price; SELL adds (qty_sold/100)*norm_price
    --   realized_pnl = (qty_sold/100) * (sell_norm - cost_basis_norm)
    position_before           NUMERIC(18,4) NOT NULL DEFAULT 0,
    position_after            NUMERIC(18,4) NOT NULL DEFAULT 0
        CHECK (position_after >= 0),  -- long-only: SELL cannot exceed total_qty
    cash_before               NUMERIC(18,4) NOT NULL DEFAULT 0,
    cash_after                NUMERIC(18,4) NOT NULL DEFAULT 0,
    total_qty_before          NUMERIC(12,4) NOT NULL DEFAULT 0,
    total_qty_after           NUMERIC(12,4) NOT NULL DEFAULT 0,

    -- P&L in normalized units ((qty_sold/100) * (sell_norm - cost_basis_norm))
    realized_pnl              NUMERIC(18,4) NOT NULL DEFAULT 0,

    -- Strategy signal context
    signal_value              NUMERIC(18,6),
    signal_reason             TEXT,

    -- attrition
    -- Slippage = |fill_price - close| / 100: how far the worst-case OHLC
    -- fill deviates from the day's close, normalized to per-100-shares
    -- scale (same scale as fee). ≥ 0 for both BUY (paid more) and SELL
    -- (received less).
    slippage                  NUMERIC(18,6),
    -- Fee = 0.2% of the BUY notional (normalized money = 0.002 × (qty/100)
    -- × normalized_fill_price). Applied to BUY only; 0 for SELL. Deducted
    -- from cash_after on BUY.
    fee                       NUMERIC(18,6),

    CONSTRAINT pk_trade_decision PRIMARY KEY (seq_id, decision_no),
    CONSTRAINT fk_trade_decision_seq FOREIGN KEY (seq_id)
        REFERENCES strategy.strategy_identity(seq_id) ON DELETE CASCADE
);

COMMENT ON TABLE  strategy.trade_decision                  IS 'Ordered trade decisions within a strategy_identity (per-code). ALL financial metrics in normalized units (base=100 at first BUY). position_after >= 0 enforced at DB level (long-only).';
COMMENT ON COLUMN strategy.trade_decision.seq_id          IS 'FK → strategy_identity.seq_id. Identifies the (strategy, code) run this decision belongs to.';
COMMENT ON COLUMN strategy.trade_decision.decision_no     IS '1-based ordinal of this decision within its seq (chronological). PK together with seq_id.';
COMMENT ON COLUMN strategy.trade_decision.side            IS 'Trade direction: BUY or SELL.';
COMMENT ON COLUMN strategy.trade_decision.qty             IS 'Quantity traded. For BUY: equals confidence (0-100). For SELL: actual quantity sold = (confidence/100) * total_qty_before (a fraction of the cumulative position, never shorts). Can exceed 100 when total_qty_before > 100.';
COMMENT ON COLUMN strategy.trade_decision.exec_date       IS 'Date the order was executed/filled (same day as the signal — worst-case fill derived from the day OHLC).';
COMMENT ON COLUMN strategy.trade_decision.fill_price      IS 'Execution (fill) price (actual).';
COMMENT ON COLUMN strategy.trade_decision.normalized_fill_price IS 'fill_price rebased to 100 at the FIRST BUY fill of this run (= fill_price / strategy_results.first_buy_fill_price * 100). First BUY = 100; later fills read as % change from entry.';
COMMENT ON COLUMN strategy.trade_decision.normalized_mean_buy_price IS 'Weighted-avg BUY normalized_fill_price across all historical BUYs still in the remaining position (mean remaining buy price weighted on qty). For BUY: the post-BUY cost basis (new weighted average including this BUY). For SELL: the pre-SELL cost basis used to compute realized_pnl (= (qty_sold/100) * (sell_norm - this_value), where qty_sold = (confidence/100) * total_qty_before); stays constant across partial SELLs and is the last cost basis before reset to 0 when total_qty reaches 0. A PRICE, not money — NOT divided by 100.';
COMMENT ON COLUMN strategy.trade_decision.position_before IS 'Mark-to-market position (normalized money = (total_qty/100) × normalized_fill_price) immediately before this decision. Grows/decreases with price between trades.';
COMMENT ON COLUMN strategy.trade_decision.position_after  IS 'Mark-to-market position immediately after. CHECK >= 0 enforces long-only (SELL cannot exceed total_qty).';
COMMENT ON COLUMN strategy.trade_decision.cash_before     IS 'Cumulative cash (normalized money) immediately before this decision. = running sum of (qty/100) × normalized_fill_price (BUY subtracts (qty/100)*norm_price; SELL adds (qty_sold/100)*norm_price).';
COMMENT ON COLUMN strategy.trade_decision.cash_after      IS 'Cumulative cash (normalized money) immediately after. BUY → cash_before - (qty/100)*norm_price; SELL → cash_before + (qty_sold/100)*norm_price (closes a fraction of current total_qty).';
COMMENT ON COLUMN strategy.trade_decision.total_qty_before IS 'Cumulative quantity (in qty/confidence units, NOT /100) immediately before this decision. = running sum of BUY qty minus SELL qty_sold.';
COMMENT ON COLUMN strategy.trade_decision.total_qty_after  IS 'Cumulative quantity (in qty/confidence units, NOT /100) immediately after. For BUY: total_qty_before + qty. For SELL: total_qty_before - qty (= total_qty_before * (1 - confidence/100)).';
COMMENT ON COLUMN strategy.trade_decision.realized_pnl    IS 'Realized P&L on SELL in normalized money = (qty_sold/100) * (sell_norm - cost_basis_norm), where qty_sold = (confidence/100) * total_qty_before and cost_basis_norm is the weighted-avg BUY normalized_fill_price. 0 for BUY.';
COMMENT ON COLUMN strategy.trade_decision.signal_value    IS 'Numeric value of the triggering signal (e.g. MA spread).';
COMMENT ON COLUMN strategy.trade_decision.signal_reason   IS 'Human-readable reason the signal fired (e.g. "MA5 crossed above MA60 by 2.1%").';
COMMENT ON COLUMN strategy.trade_decision.slippage        IS 'Slippage = |fill_price - close| / 100: how far the worst-case OHLC fill deviates from the day close, normalized to per-100-shares scale (same scale as fee). ≥ 0 for both BUY (paid more) and SELL (received less).';
COMMENT ON COLUMN strategy.trade_decision.fee             IS 'Fee = 0.2% of BUY notional (normalized money = 0.002 × (qty/100) × normalized_fill_price). BUY only; 0 for SELL. Deducted from cash_after on BUY.';

-- ----------------------------------------------------------------------------
-- Indexes
-- ----------------------------------------------------------------------------
-- (a) strategy_identity: look up runs by name/number, and per-code latest
CREATE INDEX IF NOT EXISTS idx_strategy_identity_name_no
    ON strategy.strategy_identity (strategy_name, seq_no);

CREATE INDEX IF NOT EXISTS idx_strategy_identity_type_code_no
    ON strategy.strategy_identity (sec_type, code, seq_no DESC);

-- (b) strategy_results: per-code latest lookup without touching strategy_identity
CREATE INDEX IF NOT EXISTS idx_strategy_results_type_code
    ON strategy.strategy_results (sec_type, code);

-- (c) trade_decision: chronological lookup within a seq
CREATE INDEX IF NOT EXISTS idx_trade_decision_seq_exec_date
    ON strategy.trade_decision (seq_id, exec_date);

-- ----------------------------------------------------------------------------
-- Table: strategy_daily
--   Daily portfolio state for one (seq_id, trade_date). One row per trading
--   day from the first BUY date to the end of the backtest period.
--
--   For each day, the portfolio state (total_qty, cash, cost_basis_norm) is
--   carried forward from the last decision executed on or before that day.
--   If a decision was executed on that day, the state is updated to that
--   decision's after-state.
--
--   unrealized_pnl = (total_qty / 100) × (normalized_close − cost_basis_norm)
--     = the P&L if ALL remaining position were sold at the day's close price.
--     This is the mark-to-market paper P&L of the open position.
--
--   total_pnl = realized_pnl_cum + unrealized_pnl
--     = the total P&L (realized + paper) as of the day's close.
--
--   is_decision_day / decision_no link to the trade_decision executed on
--   that day (NULL if no decision). ON DELETE SET NULL so removing a decision
--   just clears the link rather than cascading.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS strategy.strategy_daily (
    seq_id              BIGINT        NOT NULL,
    trade_date          DATE          NOT NULL,

    close_price         NUMERIC(18,6) NOT NULL,
    normalized_close    NUMERIC(18,6) NOT NULL,  -- close / first_buy_fill_price * 100

    normalized_mean_buy_price NUMERIC(18,6) NOT NULL,
    normalized_mean_buy_period NUMERIC(18,6) NOT NULL,


    -- Portfolio state carried from last decision (ALL normalized money)
    total_qty           NUMERIC(12,4) NOT NULL DEFAULT 0,
    cost_basis_norm     NUMERIC(18,6) NOT NULL DEFAULT 0,  -- weighted-avg BUY norm price
    position_value      NUMERIC(18,4) NOT NULL DEFAULT 0,  -- (total_qty/100) * normalized_close
    cash                NUMERIC(18,4) NOT NULL DEFAULT 0,
    realized_pnl_cum    NUMERIC(18,4) NOT NULL DEFAULT 0,  -- cumulative realized P&L through this date

    -- unrealized_pnl = (total_qty/100) * (normalized_close - cost_basis_norm)
    -- "as if all remaining position sold on the day" at the close price.
    unrealized_pnl      NUMERIC(18,4) NOT NULL DEFAULT 0,
    total_pnl           NUMERIC(18,4) NOT NULL DEFAULT 0,  -- realized_pnl_cum + unrealized_pnl
    return_rate         NUMERIC(18,6) NOT NULL DEFAULT 0,
    sharpe_ratio         NUMERIC(18,6) NOT NULL DEFAULT 0,
    sharpe_ratio_255d         NUMERIC(18,6) NOT NULL DEFAULT 0,
    sharpe_ratio_500d         NUMERIC(18,6) NOT NULL DEFAULT 0,

    -- Decision linkage (NULL if no decision on this day)
    is_decision_day     BOOLEAN       NOT NULL DEFAULT FALSE,
    decision_no         INTEGER,

    CONSTRAINT pk_strategy_daily PRIMARY KEY (seq_id, trade_date),
    CONSTRAINT fk_strategy_daily_seq FOREIGN KEY (seq_id)
        REFERENCES strategy.strategy_identity(seq_id) ON DELETE CASCADE,
    CONSTRAINT fk_strategy_daily_decision FOREIGN KEY (seq_id, decision_no)
        REFERENCES strategy.trade_decision(seq_id, decision_no) ON DELETE SET NULL
);

COMMENT ON TABLE  strategy.strategy_daily              IS 'Daily portfolio state per (seq_id, trade_date). unrealized_pnl = (total_qty/100) * (normalized_close - cost_basis_norm) — as if all remaining position sold at the day''s close. Computed from OHLC series + decisions by the backtest runner.';
COMMENT ON COLUMN strategy.strategy_daily.seq_id       IS 'FK → strategy_identity.seq_id.';
COMMENT ON COLUMN strategy.strategy_daily.trade_date   IS 'Trading day (one row per day from first BUY to end of backtest).';
COMMENT ON COLUMN strategy.strategy_daily.close_price  IS 'Actual close price on this day.';
COMMENT ON COLUMN strategy.strategy_daily.normalized_close IS 'close_price rebased to 100 at the first BUY fill (= close / first_buy_fill_price * 100). Same anchor as trade_decision.normalized_fill_price.';
COMMENT ON COLUMN strategy.strategy_daily.total_qty    IS 'Cumulative quantity (in qty/confidence units, NOT /100) carried from the last decision on or before this day. 0 before first BUY or after full liquidation.';
COMMENT ON COLUMN strategy.strategy_daily.cost_basis_norm IS 'Weighted-avg BUY normalized_fill_price carried from the last decision. For BUY: post-BUY weighted average. For SELL: pre-SELL cost basis (stays constant across partial SELLs). Resets to 0 when total_qty reaches 0.';
COMMENT ON COLUMN strategy.strategy_daily.position_value IS 'Mark-to-market position value = (total_qty / 100) × normalized_close. Tracks the current value of the open position.';
COMMENT ON COLUMN strategy.strategy_daily.cash         IS 'Cumulative cash (normalized money) carried from the last decision. BUY subtracts (qty/100)*norm_price; SELL adds (qty_sold/100)*norm_price.';
COMMENT ON COLUMN strategy.strategy_daily.realized_pnl_cum IS 'Cumulative realized P&L (sum of realized_pnl for all SELLs up to and including this day).';
COMMENT ON COLUMN strategy.strategy_daily.unrealized_pnl IS 'Paper P&L if all remaining position were sold at the day''s close = (total_qty / 100) × (normalized_close − cost_basis_norm). 0 when total_qty = 0 (no open position).';
COMMENT ON COLUMN strategy.strategy_daily.total_pnl    IS 'Total P&L as of the day''s close = realized_pnl_cum + unrealized_pnl.';
COMMENT ON COLUMN strategy.strategy_daily.return_rate  IS 'ANNUALIZED return on capital = (total_pnl / capital_deployed / max(mean_holding_days, 1)) × 255. capital_deployed = (total_qty / 100) * normalized_mean_buy_price (current cost basis × shares). mean_holding_days = (trade_date − first_buy_date).days − normalized_mean_buy_period (weighted-avg BUY period since first BUY). 0 when total_qty = 0 (no capital at risk) or mean_holding_days <= 0.';
COMMENT ON COLUMN strategy.strategy_daily.is_decision_day IS 'TRUE if a BUY or SELL decision was executed on this day.';
COMMENT ON COLUMN strategy.strategy_daily.decision_no  IS 'decision_no of the trade executed on this day (FK → trade_decision). NULL if no decision. ON DELETE SET NULL.';

CREATE INDEX IF NOT EXISTS idx_strategy_daily_seq_date
    ON strategy.strategy_daily (seq_id, trade_date);

CREATE INDEX IF NOT EXISTS idx_strategy_daily_decision
    ON strategy.strategy_daily (seq_id, decision_no)
    WHERE is_decision_day = TRUE;

-- Idempotent migration: add normalized_mean_buy_period to strategy_daily for
-- existing DBs (the column is already in the CREATE TABLE above for fresh
-- DBs). Mirrors normalized_mean_buy_price but in the TIME dimension: the
-- weighted-avg BUY period (calendar days since the first BUY), weighted on
-- remaining qty. BUY updates the weighted average; SELL keeps it constant
-- (proportional reduction); resets to 0 on full liquidation. Existing rows
-- get 0 (backfilled by re-running the backtest). Used as the mean buy TIME
-- so holding time = (trade_date − first_buy_date).days −
-- normalized_mean_buy_period, enabling return-per-holding-time.
ALTER TABLE strategy.strategy_daily
    ADD COLUMN IF NOT EXISTS normalized_mean_buy_period NUMERIC(18,6)
    NOT NULL DEFAULT 0;

COMMENT ON COLUMN strategy.strategy_daily.normalized_mean_buy_price IS 'Weighted-avg BUY normalized_fill_price carried from the last decision (mirrors trade_decision.normalized_mean_buy_price). BUY → post-BUY weighted average; SELL → pre-SELL cost basis (constant across partial SELLs); resets to 0 when total_qty reaches 0.';
COMMENT ON COLUMN strategy.strategy_daily.normalized_mean_buy_period IS 'Weighted-avg BUY period in calendar days since the first BUY (first BUY = 0), weighted on remaining qty. Mirrors normalized_mean_buy_price in the TIME dimension: BUY → (tq_before·period + qty·this_buy_period) / tq_after; SELL → unchanged (proportional reduction); resets to 0 on full liquidation. Mean holding time = (trade_date − first_buy_date).days − this value; used as the mean buy time to derive per-holding-period return.';

-- Idempotent migration: add return_rate column to strategy_daily for existing
-- DBs (the column is already in the CREATE TABLE above for fresh DBs).
-- return_rate = ANNUALIZED return on capital =
-- (total_pnl / capital_deployed / max(mean_holding_days, 1)) × 255, where
-- capital_deployed = (total_qty / 100) * normalized_mean_buy_price and
-- mean_holding_days = (trade_date - first_buy_date).days - normalized_mean_buy_period.
-- 0 when total_qty = 0 or mean_holding_days <= 0. Existing rows get 0
-- (backfilled by re-running the backtest).
ALTER TABLE strategy.strategy_daily
    ADD COLUMN IF NOT EXISTS return_rate NUMERIC(18,6) NOT NULL DEFAULT 0;

COMMENT ON COLUMN strategy.strategy_daily.return_rate IS 'ANNUALIZED return on capital = (total_pnl / capital_deployed / max(mean_holding_days, 1)) × 255. capital_deployed = (total_qty / 100) * normalized_mean_buy_price (current cost basis × shares). mean_holding_days = (trade_date − first_buy_date).days − normalized_mean_buy_period (weighted-avg BUY period since first BUY). 0 when total_qty = 0 (no capital at risk) or mean_holding_days <= 0.';

-- Idempotent migration: add sharpe_ratio columns to strategy_daily for
-- existing DBs (the columns are already in the CREATE TABLE above for fresh
-- DBs). Each is the annualized Sharpe ratio (×√255, risk-free=0) of daily
-- Δtotal_pnl over a different window:
--   sharpe_ratio      — cumulative over ALL history up to this trade_date
--   sharpe_ratio_255d — rolling 255-trading-day window (~1 year)
--   sharpe_ratio_500d — rolling 500-trading-day window (~2 years)
-- Daily Δtotal_pnl = total_pnl[t] − total_pnl[t−1] captures both realized
-- gains/losses (from SELLs) and mark-to-market changes. The first day has no
-- delta → 0. Windows with < 2 deltas or σ = 0 → 0. Existing rows get 0
-- (backfilled by re-running the backtest).
ALTER TABLE strategy.strategy_daily
    ADD COLUMN IF NOT EXISTS sharpe_ratio NUMERIC(18,6) NOT NULL DEFAULT 0;
ALTER TABLE strategy.strategy_daily
    ADD COLUMN IF NOT EXISTS sharpe_ratio_255d NUMERIC(18,6) NOT NULL DEFAULT 0;
ALTER TABLE strategy.strategy_daily
    ADD COLUMN IF NOT EXISTS sharpe_ratio_500d NUMERIC(18,6) NOT NULL DEFAULT 0;

COMMENT ON COLUMN strategy.strategy_daily.sharpe_ratio IS 'Annualized Sharpe ratio (×√255, risk-free=0) of daily Δtotal_pnl over ALL history up to this trade_date. Δtotal_pnl = total_pnl[t] − total_pnl[t−1] captures realized gains/losses + MTM changes. 0 when < 2 deltas or σ = 0.';
COMMENT ON COLUMN strategy.strategy_daily.sharpe_ratio_255d IS 'Annualized Sharpe ratio (×√255, risk-free=0) of daily Δtotal_pnl over a rolling 255-trading-day window (~1 year) ending on this trade_date. 0 when < 2 deltas in window or σ = 0.';
COMMENT ON COLUMN strategy.strategy_daily.sharpe_ratio_500d IS 'Annualized Sharpe ratio (×√255, risk-free=0) of daily Δtotal_pnl over a rolling 500-trading-day window (~2 years) ending on this trade_date. 0 when < 2 deltas in window or σ = 0.';

-- ----------------------------------------------------------------------------
-- Migration: drop signal_date from trade_decision (fill now happens on the
-- signal day itself using a worst-case OHLC-derived price — no T+1 lag).
-- The view must be dropped first (it depends on the column) and is recreated
-- by the CREATE OR REPLACE VIEW later in this script.
-- ----------------------------------------------------------------------------
DROP VIEW IF EXISTS strategy.v_trade_decision_full;
ALTER TABLE strategy.trade_decision DROP CONSTRAINT IF EXISTS chk_trade_decision_dates;
ALTER TABLE strategy.trade_decision DROP COLUMN IF EXISTS signal_date;
DROP INDEX IF EXISTS strategy.idx_trade_decision_seq_signal_date;
CREATE INDEX IF NOT EXISTS idx_trade_decision_seq_exec_date
    ON strategy.trade_decision (seq_id, exec_date);

-- ============================================================================
--  Internal Risk Analytics
--  Computes strategy risk metrics from the trade_decision history.
--
--  Two tables:
--    strategy.strategy_risks    — per-(seq_id, code) RISK-SPECIFIC metrics:
--                                    chronological concentration of gains/losses,
--                                    exponential risk score, top gain/loss trades,
--                                    price-based drawdowns. P&L summary cols
--                                    (total_realized_pnl / total_abs_pnl / n_sells
--                                    / n_buys) MOVED to strategy_results.
--    strategy.strategy_risk_period — per-period (year / season / month)
--                                    aggregations of gains/losses with the top
--                                    trades in each period.
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
--    falls in the worst 30-day rolling window (still computed for the UI
--    hotspot flag, but NO LONGER drives risk_score directly).
--
--  risk_score is now an EXPONENTIAL ROLLING-WINDOW score over multiple
--    time horizons. For each window W ∈ {1d, 30d, 90d, 365d}, the worst
--    W-day rolling LOSS (realized, and separately unrealized MTM dip +
--    window-end residual) contributes:
--        exp(k · loss_fraction / threshold_W) - 1
--    where loss_fraction = |loss| / total_abs_pnl (LOSSES ONLY), threshold_W
--    comes from a log-curve fit through (month=25%, season=50%, year=75%
--    of total_abs_pnl), and k = ln 2 so hitting a threshold = +1.0.
--    Unrealized contributions are weighted at 30% vs realized. The score
--    thus scales EXPONENTIALLY with loss severity per horizon and rewards
--    spread-out (non-clustered) loss patterns.
-- ============================================================================
CREATE TABLE IF NOT EXISTS strategy.strategy_risks (
    seq_id                        BIGINT        NOT NULL,
    code                          TEXT          NOT NULL,

    -- Top-3 gain / loss / confidence BUY trade references
    -- (FK → trade_decision(seq_id, decision_no)).
    -- seq_id is shared with trade_decision (strategy_risks.seq_id IS the trade's
    -- seq_id), so only decision_no is stored. NULL when fewer than 3 trades
    -- of that side exist. The UI JOINs to trade_decision to fetch pnl /
    -- exec_date / signal_reason / qty on demand — no denormalized copies here.
    pnl_gain_1st_decision_no      INTEGER,
    pnl_gain_2nd_decision_no      INTEGER,
    pnl_gain_3rd_decision_no      INTEGER,
    pnl_loss_1st_decision_no      INTEGER,
    pnl_loss_2nd_decision_no      INTEGER,
    pnl_loss_3rd_decision_no      INTEGER,
    -- Top-3 highest-confidence BUYs (by qty descending; qty = confidence 0-100)
    confidence_buy_1st_decision_no      INTEGER,
    confidence_buy_2nd_decision_no      INTEGER,
    confidence_buy_3rd_decision_no      INTEGER,

    -- Chronological concentration
    --   max_30d_abs_pnl: the largest 30-day rolling sum of |realized_pnl|
    --   concentration_ratio = max_30d_abs_pnl / total_abs_pnl  (∈ [0,1])
    --   1.0 = all P&L crammed into a single 30-day window (max risk)
    --   ~0  = perfectly spread across time (min risk)
    max_30d_abs_pnl               NUMERIC(24,4),
    concentration_ratio           NUMERIC(10,6),  -- [0,1]
    concentration_window_start    DATE,           -- start of the worst 30d window
    concentration_window_end      DATE,           -- end of the worst 30d window

    -- Exponential rolling-window risk score (see table-level comment).
    --   The top-3 cumulative-P&L drawdown DATES + MAGNITUDES below are still
    --   persisted for UI display, but the risk_score itself is now computed
    --   from multi-horizon rolling losses (not concentration * drawdown).
    drawdown_1st_date             DATE,  -- trough date of the worst cumulative-P&L drawdown
    drawdown_2nd_date             DATE,  -- trough date of the 2nd-worst drawdown
    drawdown_3rd_date             DATE,  -- trough date of the 3rd-worst drawdown
    -- Per-episode drawdown magnitude (trough_cum_pnl - peak_cum_pnl, <= 0).
    -- 1st is the worst drawdown magnitude (for UI display only).
    drawdown_1st_val              NUMERIC(24,4),  -- worst drawdown magnitude (<= 0)
    drawdown_2nd_val              NUMERIC(24,4),  -- 2nd-worst drawdown magnitude (<= 0)
    drawdown_3rd_val              NUMERIC(24,4),  -- 3rd-worst drawdown magnitude (<= 0)
    risk_score                    NUMERIC(24,4),  -- exponential rolling-window score (see table comment)

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

    -- Risk grade (derived from the absolute risk_score on the new
    --   exponential scale, k = ln 2 ⇒ one window at threshold = 1.0):
    --   LITTLE = criteria-based (almost no losses + stable gains + profitable)
    --   < 1.0 = LOW, 1.0–3.0 = MODERATE, 3.0–6.0 = ELEVATED, > 6.0 = HIGH
    risk_grade                    TEXT
        CHECK (risk_grade IN ('LITTLE', 'LOW', 'MODERATE', 'ELEVATED', 'HIGH')),

    computed_at                   TIMESTAMPTZ   NOT NULL DEFAULT now(),

    CONSTRAINT pk_strategy_risks PRIMARY KEY (seq_id, code),
    CONSTRAINT fk_strategy_risks_seq FOREIGN KEY (seq_id)
        REFERENCES strategy.strategy_identity(seq_id) ON DELETE CASCADE,
    -- FK: top-3 gain/loss trades → trade_decision(seq_id, decision_no).
    -- ON DELETE SET NULL: if a decision is removed, the ref just goes NULL
    -- rather than cascading the delete into the risk row.
    CONSTRAINT fk_risks_pnl_gain_1st FOREIGN KEY (seq_id, pnl_gain_1st_decision_no)
        REFERENCES strategy.trade_decision(seq_id, decision_no) ON DELETE SET NULL,
    CONSTRAINT fk_risks_pnl_gain_2nd FOREIGN KEY (seq_id, pnl_gain_2nd_decision_no)
        REFERENCES strategy.trade_decision(seq_id, decision_no) ON DELETE SET NULL,
    CONSTRAINT fk_risks_pnl_gain_3rd FOREIGN KEY (seq_id, pnl_gain_3rd_decision_no)
        REFERENCES strategy.trade_decision(seq_id, decision_no) ON DELETE SET NULL,
    CONSTRAINT fk_risks_pnl_loss_1st FOREIGN KEY (seq_id, pnl_loss_1st_decision_no)
        REFERENCES strategy.trade_decision(seq_id, decision_no) ON DELETE SET NULL,
    CONSTRAINT fk_risks_pnl_loss_2nd FOREIGN KEY (seq_id, pnl_loss_2nd_decision_no)
        REFERENCES strategy.trade_decision(seq_id, decision_no) ON DELETE SET NULL,
    CONSTRAINT fk_risks_pnl_loss_3rd FOREIGN KEY (seq_id, pnl_loss_3rd_decision_no)
        REFERENCES strategy.trade_decision(seq_id, decision_no) ON DELETE SET NULL,
    CONSTRAINT fk_risks_confidence_buy_1st FOREIGN KEY (seq_id, confidence_buy_1st_decision_no)
        REFERENCES strategy.trade_decision(seq_id, decision_no) ON DELETE SET NULL,
    CONSTRAINT fk_risks_confidence_buy_2nd FOREIGN KEY (seq_id, confidence_buy_2nd_decision_no)
        REFERENCES strategy.trade_decision(seq_id, decision_no) ON DELETE SET NULL,
    CONSTRAINT fk_risks_confidence_buy_3rd FOREIGN KEY (seq_id, confidence_buy_3rd_decision_no)
        REFERENCES strategy.trade_decision(seq_id, decision_no) ON DELETE SET NULL,
    CONSTRAINT chk_risk_concentration CHECK (
        concentration_ratio IS NULL OR
        (concentration_ratio >= 0 AND concentration_ratio <= 1)
    ),
    -- Price-based drops are signed fractional ratios (<= 0); 0 = no drop.
    CONSTRAINT chk_risk_drop_unzero CHECK (
        deepest_drop_since_unzero_pos IS NULL OR
        deepest_drop_since_unzero_pos <= 0
    ),
    CONSTRAINT chk_risk_drop_last_buy CHECK (
        deepest_drop_since_last_buy IS NULL OR
        deepest_drop_since_last_buy <= 0
    ),
    -- Drawdown magnitudes are signed P&L deltas (trough - peak, <= 0).
    CONSTRAINT chk_risk_drawdown_1st_val CHECK (
        drawdown_1st_val IS NULL OR drawdown_1st_val <= 0
    ),
    CONSTRAINT chk_risk_drawdown_2nd_val CHECK (
        drawdown_2nd_val IS NULL OR drawdown_2nd_val <= 0
    ),
    CONSTRAINT chk_risk_drawdown_3rd_val CHECK (
        drawdown_3rd_val IS NULL OR drawdown_3rd_val <= 0
    )
);

-- ----------------------------------------------------------------------------
-- Idempotent migration (part A): add the drawdown date columns BEFORE the
--   COMMENTs below (COMMENT ON COLUMN requires the column to exist on
--   pre-existing DBs where CREATE TABLE IF NOT EXISTS is a no-op). The
--   chk_risk_drawdown constraint (referencing the legacy max_drawdown column)
--   is dropped here too; max_drawdown itself is dropped in part C below once
--   the v_strategy_risk_full view no longer references it.
-- ----------------------------------------------------------------------------
ALTER TABLE strategy.strategy_risks DROP CONSTRAINT IF EXISTS chk_risk_drawdown;
-- Drop the legacy CHECK constraint left over from when this table was named
-- strategy_risk_seq (it only allowed LOW/MODERATE/ELEVATED/HIGH, missing the
-- LITTLE grade). The current column-level CHECK (strategy_risks_risk_grade_check)
-- already covers all 5 grades correctly, so this stale duplicate must be removed
-- or both checks must pass simultaneously — which fails for LITTLE rows.
ALTER TABLE strategy.strategy_risks DROP CONSTRAINT IF EXISTS strategy_risk_seq_risk_grade_check;
ALTER TABLE strategy.strategy_risks ADD COLUMN IF NOT EXISTS drawdown_1st_date DATE;
ALTER TABLE strategy.strategy_risks ADD COLUMN IF NOT EXISTS drawdown_2nd_date DATE;
ALTER TABLE strategy.strategy_risks ADD COLUMN IF NOT EXISTS drawdown_3rd_date DATE;
ALTER TABLE strategy.strategy_risks ADD COLUMN IF NOT EXISTS drawdown_1st_val NUMERIC(24,4);
ALTER TABLE strategy.strategy_risks ADD COLUMN IF NOT EXISTS drawdown_2nd_val NUMERIC(24,4);
ALTER TABLE strategy.strategy_risks ADD COLUMN IF NOT EXISTS drawdown_3rd_val NUMERIC(24,4);
ALTER TABLE strategy.strategy_risks DROP CONSTRAINT IF EXISTS chk_risk_drawdown_1st_val;
ALTER TABLE strategy.strategy_risks DROP CONSTRAINT IF EXISTS chk_risk_drawdown_2nd_val;
ALTER TABLE strategy.strategy_risks DROP CONSTRAINT IF EXISTS chk_risk_drawdown_3rd_val;
ALTER TABLE strategy.strategy_risks ADD CONSTRAINT chk_risk_drawdown_1st_val
    CHECK (drawdown_1st_val IS NULL OR drawdown_1st_val <= 0);
ALTER TABLE strategy.strategy_risks ADD CONSTRAINT chk_risk_drawdown_2nd_val
    CHECK (drawdown_2nd_val IS NULL OR drawdown_2nd_val <= 0);
ALTER TABLE strategy.strategy_risks ADD CONSTRAINT chk_risk_drawdown_3rd_val
    CHECK (drawdown_3rd_val IS NULL OR drawdown_3rd_val <= 0);

COMMENT ON TABLE  strategy.strategy_risks                  IS 'Per-(seq, code) RISK-SPECIFIC metrics: chronological P&L concentration, exponential risk score, top-3 gain/loss trade FK refs, price-based drawdowns. P&L summary (total_realized_pnl / total_abs_pnl / n_sells / n_buys) moved to strategy_results.';
COMMENT ON COLUMN strategy.strategy_risks.seq_id           IS 'FK → strategy_identity.seq_id.';
COMMENT ON COLUMN strategy.strategy_risks.code             IS 'Security code this risk row pertains to (denormalized for fast UI display).';
COMMENT ON COLUMN strategy.strategy_risks.pnl_gain_1st_decision_no IS 'decision_no of the 1st-largest single-trade gain (max realized_pnl among SELLs). FK → trade_decision(seq_id, decision_no). NULL if no SELL exists.';
COMMENT ON COLUMN strategy.strategy_risks.pnl_gain_2nd_decision_no IS 'decision_no of the 2nd-largest single-trade gain. FK → trade_decision(seq_id, decision_no). NULL if fewer than 2 SELLs.';
COMMENT ON COLUMN strategy.strategy_risks.pnl_gain_3rd_decision_no IS 'decision_no of the 3rd-largest single-trade gain. FK → trade_decision(seq_id, decision_no). NULL if fewer than 3 SELLs.';
COMMENT ON COLUMN strategy.strategy_risks.pnl_loss_1st_decision_no IS 'decision_no of the 1st-largest single-trade loss (min realized_pnl among SELLs). FK → trade_decision(seq_id, decision_no). NULL if no SELL exists.';
COMMENT ON COLUMN strategy.strategy_risks.pnl_loss_2nd_decision_no IS 'decision_no of the 2nd-largest single-trade loss. FK → trade_decision(seq_id, decision_no). NULL if fewer than 2 SELLs.';
COMMENT ON COLUMN strategy.strategy_risks.pnl_loss_3rd_decision_no IS 'decision_no of the 3rd-largest single-trade loss. FK → trade_decision(seq_id, decision_no). NULL if fewer than 3 SELLs.';
COMMENT ON COLUMN strategy.strategy_risks.confidence_buy_1st_decision_no IS 'decision_no of the highest-confidence BUY (max qty; qty = confidence 0-100). FK → trade_decision(seq_id, decision_no). NULL if no BUY exists.';
COMMENT ON COLUMN strategy.strategy_risks.confidence_buy_2nd_decision_no IS 'decision_no of the 2nd-highest-confidence BUY. FK → trade_decision(seq_id, decision_no). NULL if fewer than 2 BUYs.';
COMMENT ON COLUMN strategy.strategy_risks.confidence_buy_3rd_decision_no IS 'decision_no of the 3rd-highest-confidence BUY. FK → trade_decision(seq_id, decision_no). NULL if fewer than 3 BUYs.';
COMMENT ON COLUMN strategy.strategy_risks.max_30d_abs_pnl  IS 'The largest 30-day rolling sum of |realized_pnl| — peak P&L concentration window.';
COMMENT ON COLUMN strategy.strategy_risks.concentration_ratio IS 'max_30d_abs_pnl / total_abs_pnl (total_abs_pnl on strategy_results). 1.0 = all P&L in one 30-day window (max risk); ~0 = evenly spread (min risk).';
COMMENT ON COLUMN strategy.strategy_risks.concentration_window_start IS 'Start date of the worst (most concentrated) 30-day P&L window.';
COMMENT ON COLUMN strategy.strategy_risks.concentration_window_end   IS 'End date of the worst 30-day P&L window.';
COMMENT ON COLUMN strategy.strategy_risks.drawdown_1st_date IS 'Trough date (SELL exec_date where cumulative realized P&L bottomed) of the WORST peak-to-trough drawdown in cumulative realized P&L. NULL if there is no drawdown episode.';
COMMENT ON COLUMN strategy.strategy_risks.drawdown_2nd_date IS 'Trough date of the 2nd-worst cumulative-P&L drawdown. NULL if fewer than 2 drawdown episodes.';
COMMENT ON COLUMN strategy.strategy_risks.drawdown_3rd_date IS 'Trough date of the 3rd-worst cumulative-P&L drawdown. NULL if fewer than 3 drawdown episodes.';
COMMENT ON COLUMN strategy.strategy_risks.drawdown_1st_val IS 'Magnitude (trough_cum_pnl - peak_cum_pnl, signed <= 0) of the WORST cumulative-P&L drawdown. For UI display only (risk_score now uses rolling-window losses). NULL if no drawdown episode.';
COMMENT ON COLUMN strategy.strategy_risks.drawdown_2nd_val IS 'Magnitude (trough_cum_pnl - peak_cum_pnl, signed <= 0) of the 2nd-worst cumulative-P&L drawdown. NULL if fewer than 2 drawdown episodes.';
COMMENT ON COLUMN strategy.strategy_risks.drawdown_3rd_val IS 'Magnitude (trough_cum_pnl - peak_cum_pnl, signed <= 0) of the 3rd-worst cumulative-P&L drawdown. NULL if fewer than 3 drawdown episodes.';
COMMENT ON COLUMN strategy.strategy_risks.risk_score       IS 'Exponential rolling-window risk score. For each window W in {1d,30d,90d,365d}, the worst W-day rolling LOSS (realized + unrealized MTM dip + window-end residual) contributes exp(k * loss_fraction / threshold_W) - 1, where loss_fraction = |loss|/total_abs_pnl (LOSSES ONLY), threshold_W is a log-curve fit through (month=25%, season=50%, year=75% of total_abs_pnl), k = ln 2. Unrealized weighted at 30% vs realized. Higher = more dangerous.';
COMMENT ON COLUMN strategy.strategy_risks.risk_grade       IS 'LITTLE / LOW / MODERATE / ELEVATED / HIGH — LITTLE is criteria-based (almost no losses + stable gains + profitable); otherwise derived from risk_score: <1.0 LOW, <3.0 MODERATE, <6.0 ELEVATED, else HIGH.';
COMMENT ON COLUMN strategy.strategy_risks.deepest_drop_since_unzero_pos IS 'Worst close-price peak-to-trough drawdown (signed fractional ratio, <= 0) observed during any maximal span where position > 0. Captures the worst paper-loss endured while holding. 0 = price only rose while holding.';
COMMENT ON COLUMN strategy.strategy_risks.deepest_drop_since_unzero_pos_peak_date   IS 'Biz date of the peak (running max close) from which the worst unzero-position drop was measured.';
COMMENT ON COLUMN strategy.strategy_risks.deepest_drop_since_unzero_pos_trough_date IS 'Biz date of the trough (lowest close) reached in the worst unzero-position drop.';
COMMENT ON COLUMN strategy.strategy_risks.deepest_drop_since_last_buy  IS 'Worst close-price peak-to-trough drawdown (signed fractional ratio, <= 0) from a BUY entry (seed peak = fill_price) until the next decision. Maximum adverse excursion following an entry. 0 = price never fell below the entry after a buy.';
COMMENT ON COLUMN strategy.strategy_risks.deepest_drop_since_last_buy_peak_date     IS 'Biz date of the peak (running max close, seeded by the BUY fill_price) from which the worst since-last-buy drop was measured.';
COMMENT ON COLUMN strategy.strategy_risks.deepest_drop_since_last_buy_trough_date   IS 'Biz date of the trough (lowest close) reached in the worst since-last-buy drop.';
COMMENT ON COLUMN strategy.strategy_risks.computed_at      IS 'Timestamp this risk row was computed (UTC).';

-- ----------------------------------------------------------------------------
-- Table: strategy_risk_period
--   Per-period gain/loss aggregations. One row per (seq_id, code, period_type,
--   period_value). period_type is 'year' / 'season' / 'month'.
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
    -- Mark-to-market change in unrealized_pnl during this period =
    -- unrealized_pnl(end of period) - unrealized_pnl(end of previous period).
    -- From strategy_daily. Realized + unrealized = total economic P&L for the period.
    unrealized_pnl                NUMERIC(24,4) NOT NULL DEFAULT 0,
    -- Worst (min, most negative) daily unrealized_pnl within this period —
    -- the deepest intra-period MTM loss (maximum unrealized loss). From
    -- strategy_daily. UI draws a transparent red bar for this.
    max_loss_unrealized_pnl       NUMERIC(24,4) NOT NULL DEFAULT 0,
    -- Peak (max, most positive) daily unrealized_pnl within this period —
    -- the highest intra-period MTM gain (maximum unrealized gain). From
    -- strategy_daily. UI draws a transparent green bar for this.
    max_gain_unrealized_pnl       NUMERIC(24,4) NOT NULL DEFAULT 0,
    -- Unrealized_pnl at the LAST trading day of this period (absolute level,
    -- not a change). From strategy_daily. Used by the UI to draw the
    -- period-end bar.
    end_unrealized_pnl            NUMERIC(24,4) NOT NULL DEFAULT 0,
    abs_pnl                       NUMERIC(24,4) NOT NULL DEFAULT 0,
    -- share of the run's total |P&L| that this period contributed (∈ [0,1])
    period_share                  NUMERIC(10,6),

    -- Is this period a "concentration hotspot"?
    is_concentration_hotspot      BOOLEAN       NOT NULL DEFAULT FALSE,
    is_counter_trend              BOOLEAN       NOT NULL DEFAULT FALSE,

    computed_at                   TIMESTAMPTZ   NOT NULL DEFAULT now(),

    CONSTRAINT pk_strategy_risk_period PRIMARY KEY (seq_id, code, period_type, period_value),
    CONSTRAINT fk_strategy_risk_period_seq FOREIGN KEY (seq_id)
        REFERENCES strategy.strategy_identity(seq_id) ON DELETE CASCADE,
    CONSTRAINT chk_risk_period_share CHECK (
        period_share IS NULL OR (period_share >= 0 AND period_share <= 1)
    )
);

-- Idempotent migration (part B): add unrealized_pnl BEFORE the COMMENTs
--   below (COMMENT ON COLUMN requires the column to exist on pre-existing
--   DBs where CREATE TABLE IF NOT EXISTS is a no-op).
ALTER TABLE strategy.strategy_risk_period ADD COLUMN IF NOT EXISTS unrealized_pnl NUMERIC(24,4) NOT NULL DEFAULT 0;
ALTER TABLE strategy.strategy_risk_period ADD COLUMN IF NOT EXISTS max_loss_unrealized_pnl NUMERIC(24,4) NOT NULL DEFAULT 0;
ALTER TABLE strategy.strategy_risk_period ADD COLUMN IF NOT EXISTS max_gain_unrealized_pnl NUMERIC(24,4) NOT NULL DEFAULT 0;
ALTER TABLE strategy.strategy_risk_period ADD COLUMN IF NOT EXISTS end_unrealized_pnl NUMERIC(24,4) NOT NULL DEFAULT 0;

COMMENT ON TABLE  strategy.strategy_risk_period                  IS 'Per-period (year/season/month) gain/loss aggregations with top trades and concentration flags.';
COMMENT ON COLUMN strategy.strategy_risk_period.seq_id           IS 'FK → strategy_identity.seq_id.';
COMMENT ON COLUMN strategy.strategy_risk_period.code             IS 'Security code this period row pertains to.';
COMMENT ON COLUMN strategy.strategy_risk_period.period_type      IS 'year / season / month.';
COMMENT ON COLUMN strategy.strategy_risk_period.period_value     IS 'Period label: YYYY (year), YYYY-Qn (season), YYYY-MM (month).';
COMMENT ON COLUMN strategy.strategy_risk_period.realized_pnl     IS 'Sum of realized_pnl across SELLs in this period.';
COMMENT ON COLUMN strategy.strategy_risk_period.unrealized_pnl   IS 'Mark-to-market change in unrealized_pnl during this period = unrealized_pnl(end of period) - unrealized_pnl(end of previous period). From strategy_daily. Realized + unrealized = total economic P&L for the period.';
COMMENT ON COLUMN strategy.strategy_risk_period.max_loss_unrealized_pnl IS 'Worst (min, most negative) daily unrealized_pnl within this period — the deepest intra-period MTM loss (maximum unrealized loss). From strategy_daily. UI draws a transparent red bar for this.';
COMMENT ON COLUMN strategy.strategy_risk_period.max_gain_unrealized_pnl IS 'Peak (max, most positive) daily unrealized_pnl within this period — the highest intra-period MTM gain (maximum unrealized gain). From strategy_daily. UI draws a transparent green bar for this.';
COMMENT ON COLUMN strategy.strategy_risk_period.end_unrealized_pnl IS 'Unrealized_pnl at the LAST trading day of this period (absolute level, not a change). From strategy_daily. UI draws the period-end bar for this.';
COMMENT ON COLUMN strategy.strategy_risk_period.abs_pnl          IS 'Sum of |realized_pnl| across SELLs in this period.';
COMMENT ON COLUMN strategy.strategy_risk_period.period_share     IS 'abs_pnl / total_abs_pnl (total_abs_pnl on strategy_results) for the run. High share = concentrated activity in this period.';
COMMENT ON COLUMN strategy.strategy_risk_period.is_concentration_hotspot IS 'TRUE if period_share >= 0.25 — this period accounts for a quarter+ of all P&L activity.';
COMMENT ON COLUMN strategy.strategy_risk_period.is_counter_trend IS 'TRUE if this period''s realized_pnl sign differs from the run''s total (hidden counter-trend risk).';

-- Indexes
CREATE INDEX IF NOT EXISTS idx_strategy_risks_code
    ON strategy.strategy_risks (code);

CREATE INDEX IF NOT EXISTS idx_strategy_risk_period_seq_code_type
    ON strategy.strategy_risk_period (seq_id, code, period_type, period_value);

CREATE INDEX IF NOT EXISTS idx_strategy_risk_period_hotspot
    ON strategy.strategy_risk_period (seq_id, code)
    WHERE is_concentration_hotspot = TRUE;

-- ----------------------------------------------------------------------------
-- Idempotent migration (part C): drop the legacy max_drawdown column. It is
--   now computed transiently in Python for risk_score only (not persisted).
--   The v_strategy_risk_full view (recreated just below) no longer references
--   max_drawdown, so the view is dropped FIRST to release the column
--   dependency before DROP COLUMN. The drawdown date columns (added in part
--   A) are already present, so the recreated view can select them.
-- ----------------------------------------------------------------------------
DROP VIEW IF EXISTS strategy.v_strategy_risk_full;
ALTER TABLE strategy.strategy_risks DROP COLUMN IF EXISTS max_drawdown;

-- ----------------------------------------------------------------------------
-- View: v_trade_decision_full
--   Convenience JOIN of strategy_identity + strategy_results + trade_decision so
--   readers get the run context (strategy_name, total_buy_cost, first-buy
--   anchor, params, code) + normalized_fill_price alongside each decision.
-- ----------------------------------------------------------------------------
DROP VIEW IF EXISTS strategy.v_trade_decision_full;
CREATE OR REPLACE VIEW strategy.v_trade_decision_full AS
SELECT
    d.seq_id,
    s.strategy_name,
    s.seq_no,
    s.sec_type,
    s.code,
    i.total_buy_cost,
    i.currency,
    i.first_buy_date,
    i.first_buy_fill_price,
    i.start_date        AS seq_start_date,
    i.end_date          AS seq_end_date,
    s.params            AS seq_params,
    s.status            AS seq_status,
    d.decision_no,
    d.side,
    d.qty,
    d.exec_date,
    d.fill_price,
    d.normalized_fill_price,
    d.normalized_mean_buy_price,
    d.position_before,
    d.position_after,
    d.cash_before,
    d.cash_after,
    d.total_qty_before,
    d.total_qty_after,
    d.realized_pnl,
    d.slippage,
    d.fee,
    d.signal_value,
    d.signal_reason
FROM strategy.trade_decision d
JOIN strategy.strategy_identity s ON s.seq_id = d.seq_id
JOIN strategy.strategy_results i ON i.seq_id = d.seq_id;

COMMENT ON VIEW strategy.v_trade_decision_full IS 'Convenience JOIN of strategy_identity + strategy_results + trade_decision: run context (incl. code, total_buy_cost, first-buy anchor) + normalized_fill_price alongside each decision.';

-- ----------------------------------------------------------------------------
-- View: v_strategy_risk_full
--   Convenience JOIN of strategy_identity + strategy_results + strategy_risks so
--   readers get the run context (strategy_name, params, total_buy_cost) AND
--   the P&L summary (total_realized_pnl / total_abs_pnl / n_sells / n_buys,
--   now on strategy_results) alongside the risk-specific metrics.
-- ----------------------------------------------------------------------------
DROP VIEW IF EXISTS strategy.v_strategy_risk_full;
CREATE OR REPLACE VIEW strategy.v_strategy_risk_full AS
SELECT
    r.seq_id,
    s.strategy_name,
    s.seq_no,
    s.sec_type,
    s.code,
    i.total_buy_cost,
    s.params                 AS seq_params,
    i.total_realized_pnl,
    i.total_abs_pnl,
    i.n_sells,
    i.n_buys,
    r.pnl_gain_1st_decision_no,
    r.pnl_gain_2nd_decision_no,
    r.pnl_gain_3rd_decision_no,
    r.pnl_loss_1st_decision_no,
    r.pnl_loss_2nd_decision_no,
    r.pnl_loss_3rd_decision_no,
    r.confidence_buy_1st_decision_no,
    r.confidence_buy_2nd_decision_no,
    r.confidence_buy_3rd_decision_no,
    r.max_30d_abs_pnl,
    r.concentration_ratio,
    r.concentration_window_start,
    r.concentration_window_end,
    r.drawdown_1st_date,
    r.drawdown_2nd_date,
    r.drawdown_3rd_date,
    r.drawdown_1st_val,
    r.drawdown_2nd_val,
    r.drawdown_3rd_val,
    r.risk_score,
    r.risk_grade,
    r.deepest_drop_since_unzero_pos,
    r.deepest_drop_since_unzero_pos_peak_date,
    r.deepest_drop_since_unzero_pos_trough_date,
    r.deepest_drop_since_last_buy,
    r.deepest_drop_since_last_buy_peak_date,
    r.deepest_drop_since_last_buy_trough_date,
    r.computed_at
FROM strategy.strategy_risks r
JOIN strategy.strategy_identity s ON s.seq_id = r.seq_id
JOIN strategy.strategy_results i ON i.seq_id = r.seq_id;

COMMENT ON VIEW strategy.v_strategy_risk_full IS 'Convenience JOIN of strategy_identity + strategy_results + strategy_risks: run context + P&L summary (from strategy_results) alongside risk-specific metrics (incl. top-3 gain/loss FK refs to trade_decision).';
