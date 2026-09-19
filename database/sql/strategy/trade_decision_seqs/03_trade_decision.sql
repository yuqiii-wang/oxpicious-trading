-- ============================================================================
--  Trade Decision Sequences — strategy.trade_decision
--  Ordered decisions within a strategy_identity; carries normalized_fill_price (base = 100 at the first BUY fill).
--  Part of strategy/trade_decision_seqs/ (applied in order by
--  strategy/00_init.sql; overview in 00_schema.sql).
-- ============================================================================

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
    -- qty represents confidence in 0-100 (BUY) / sold fraction of position
    -- (SELL). qty = 0 is a valid no-op SELL (legacy rows where the sold
    -- fraction rounds to 0.0000 at NUMERIC(12,4) precision).
    qty                       NUMERIC(12,4) NOT NULL CHECK (qty >= 0),

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

    -- Fault-tolerance stress comparison (bidirectional): the
    -- signal_confidence the algo WOULD have produced on this decision
    -- date if OHLC was perturbed by ft% of |Δclose| in EACH direction.
    -- NULL when no FT was applied (baseline strategy). 0 = the trade
    -- would be removed under that direction's stress (sign flipped).
    ft_stressed_conf_up       NUMERIC(8,4),
    ft_stressed_conf_down     NUMERIC(8,4),

    CONSTRAINT pk_trade_decision PRIMARY KEY (seq_id, decision_no),
    CONSTRAINT fk_trade_decision_seq FOREIGN KEY (seq_id)
        REFERENCES strategy.strategy_identity(seq_id) ON DELETE CASCADE
) PARTITION BY HASH (seq_id);

-- Native hash partitions (8) keyed by seq_id
-- Native hash partitions (8) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('strategy', 'trade_decision', 8);

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

COMMENT ON COLUMN strategy.trade_decision.ft_stressed_conf_up IS
    'FT stressed signal_confidence when decision-day OHLC moved UP by ft% of |delta_close|. NULL = no FT applied. 0 = trade would be removed under UP stress (signal sign flipped or vanished). >0 = magnitude of the stressed signal (same sign as baseline side, confidence was cut but trade still fires).';
COMMENT ON COLUMN strategy.trade_decision.ft_stressed_conf_down IS
    'FT stressed signal_confidence when decision-day OHLC moved DOWN by ft% of |delta_close|. NULL = no FT applied. 0 = trade would be removed under DOWN stress. >0 = magnitude of the stressed signal (same sign as baseline side).';

-- (c) trade_decision: chronological lookup within a seq
CREATE INDEX IF NOT EXISTS idx_trade_decision_seq_exec_date
    ON strategy.trade_decision (seq_id, exec_date);
