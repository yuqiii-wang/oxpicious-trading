-- Risk metrics — the latest strategy_risks row for one (sec_type, code),
-- preferring the is_active run then the highest seq_no. Enriched from
-- strategy_results (P&L / trade counts) and the 1st gain / 1st loss
-- trade_decision rows (top_gain_* / top_loss_*).
-- Serves GET /api/strategy/singleton/risks.
-- args:
--   sec_type: value text — 'index' | 'etf' | 'stock'
--   code: value text — the traded security code
--   strategy_name: value text — algo name (binary) or portfolio name (mixed)
  SELECT r.seq_id, r.code,
         i.total_realized_pnl, i.total_abs_pnl, i.n_sells, i.n_buys,
         r.pnl_gain_1st_decision_no, r.pnl_gain_2nd_decision_no, r.pnl_gain_3rd_decision_no,
         r.pnl_loss_1st_decision_no, r.pnl_loss_2nd_decision_no, r.pnl_loss_3rd_decision_no,
         r.confidence_buy_1st_decision_no, r.confidence_buy_2nd_decision_no, r.confidence_buy_3rd_decision_no,
         g1.realized_pnl  AS top_gain_pnl,
         g1.exec_date     AS top_gain_exec_date,
         g1.signal_reason AS top_gain_signal_reason,
         l1.realized_pnl  AS top_loss_pnl,
         l1.exec_date     AS top_loss_exec_date,
         l1.signal_reason AS top_loss_signal_reason,
         r.max_30d_abs_pnl, r.concentration_ratio,
         r.concentration_window_start, r.concentration_window_end,
         r.drawdown_1st_date, r.drawdown_2nd_date, r.drawdown_3rd_date,
         r.drawdown_1st_val, r.drawdown_2nd_val, r.drawdown_3rd_val,
         r.risk_score, r.risk_grade,
         r.ft_amplified_total_pnl,
         r.deepest_drop_since_unzero_pos,
         r.deepest_drop_since_unzero_pos_peak_date,
         r.deepest_drop_since_unzero_pos_trough_date,
         r.deepest_drop_since_last_buy,
         r.deepest_drop_since_last_buy_peak_date,
         r.deepest_drop_since_last_buy_trough_date
  FROM strategy.strategy_risks r
  JOIN strategy.strategy_identity s ON s.seq_id = r.seq_id
  JOIN strategy.strategy_results i ON i.seq_id = r.seq_id
  LEFT JOIN strategy.trade_decision g1
    ON g1.seq_id = r.seq_id AND g1.decision_no = r.pnl_gain_1st_decision_no
  LEFT JOIN strategy.trade_decision l1
    ON l1.seq_id = r.seq_id AND l1.decision_no = r.pnl_loss_1st_decision_no
  WHERE s.sec_type = {{sec_type}} AND r.code = {{code}} AND s.strategy_name = {{strategy_name}}
  ORDER BY s.is_active DESC, s.seq_no DESC
  LIMIT 1