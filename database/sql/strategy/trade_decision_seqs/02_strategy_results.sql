-- ============================================================================
--  Trade Decision Sequences — strategy.strategy_results
--  1:1 with strategy_identity: run RESULTS (dates, total_buy_cost, first-buy anchor, P&L summary).
--  Part of strategy/trade_decision_seqs/ (applied in order by
--  strategy/00_init.sql; overview in 00_schema.sql).
-- ============================================================================

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
) PARTITION BY HASH (seq_id);

-- Native hash partitions (8) keyed by seq_id
-- Native hash partitions (8) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('strategy', 'strategy_results', 8);

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

-- (b) strategy_results: per-code latest lookup without touching strategy_identity
CREATE INDEX IF NOT EXISTS idx_strategy_results_type_code
    ON strategy.strategy_results (sec_type, code);
