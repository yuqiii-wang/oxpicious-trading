-- Strategy backtest — all trade_decision rows of one strategy_seq (the seq
-- is already per-code, no code filter needed).
-- Consumed by services/strategy/backtest.ts.
-- args:
--   seq_id: value int — the strategy_seq id
  SELECT decision_no, side, exec_date,
         qty, fill_price, normalized_fill_price, normalized_mean_buy_price,
         position_before, position_after, cash_before, cash_after,
         total_qty_before, total_qty_after,
         realized_pnl, slippage, fee, signal_value, signal_reason,
         ft_stressed_conf_up, ft_stressed_conf_down
  FROM strategy.trade_decision
  WHERE seq_id = {{seq_id}}
  ORDER BY decision_no ASC
