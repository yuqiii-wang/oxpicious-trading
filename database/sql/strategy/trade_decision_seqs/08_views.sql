-- ============================================================================
--  Trade Decision Sequences — convenience views
--  v_trade_decision_full / v_strategy_risk_full.
--  Part of strategy/trade_decision_seqs/ (applied in order by
--  strategy/00_init.sql; overview in 00_schema.sql).
-- ============================================================================

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
    d.signal_reason,
    d.ft_stressed_conf_up,
    d.ft_stressed_conf_down
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
