-- ============================================================================
--  Trade Decision Sequences — strategy.strategy_daily
--  Daily portfolio state (one row per trading day); unrealized_pnl = P&L if all remaining position sold at the day's close.
--  Part of strategy/trade_decision_seqs/ (applied in order by
--  strategy/00_init.sql; overview in 00_schema.sql).
-- ============================================================================

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
) PARTITION BY HASH (seq_id);

-- Native hash partitions (32) keyed by seq_id (largest strategy table, ~3.3GB)
-- Native hash partitions (32) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p31
SELECT public.create_hash_partitions('strategy', 'strategy_daily', 32);

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

-- idx_strategy_daily_seq_date dropped: (seq_id, trade_date) is exactly the PK,
-- which already serves per-seq time-series lookups.

CREATE INDEX IF NOT EXISTS idx_strategy_daily_decision
    ON strategy.strategy_daily (seq_id, decision_no)
    WHERE is_decision_day = TRUE;

COMMENT ON COLUMN strategy.strategy_daily.normalized_mean_buy_period IS 'Weighted-avg BUY period in calendar days since the first BUY (first BUY = 0), weighted on remaining qty. Mirrors cost_basis_norm (the weighted-avg BUY normalized price) in the TIME dimension: BUY → (tq_before·period + qty·this_buy_period) / tq_after; SELL → unchanged (proportional reduction); resets to 0 on full liquidation. Mean holding time = (trade_date − first_buy_date).days − this value; used as the mean buy time to derive per-holding-period return.';
COMMENT ON COLUMN strategy.strategy_daily.sharpe_ratio IS 'Annualized Sharpe ratio (×√255, risk-free=0) of daily Δtotal_pnl over ALL history up to this trade_date. Δtotal_pnl = total_pnl[t] − total_pnl[t−1] captures realized gains/losses + MTM changes. 0 when < 2 deltas or σ = 0.';
COMMENT ON COLUMN strategy.strategy_daily.sharpe_ratio_255d IS 'Annualized Sharpe ratio (×√255, risk-free=0) of daily Δtotal_pnl over a rolling 255-trading-day window (~1 year) ending on this trade_date. 0 when < 2 deltas in window or σ = 0.';
COMMENT ON COLUMN strategy.strategy_daily.sharpe_ratio_500d IS 'Annualized Sharpe ratio (×√255, risk-free=0) of daily Δtotal_pnl over a rolling 500-trading-day window (~2 years) ending on this trade_date. 0 when < 2 deltas in window or σ = 0.';
