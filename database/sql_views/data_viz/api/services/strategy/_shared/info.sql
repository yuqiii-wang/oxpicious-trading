-- Strategy backtest — the run RESULTS row (total_buy_cost, first-buy anchor,
-- P&L summary) for one strategy_seq (1:1 with strategy_identity).
-- Consumed by services/strategy/backtest.ts.
-- args:
--   seq_id: value int — the strategy_seq id
  SELECT total_buy_cost,
         first_buy_date,
         first_buy_fill_price,
         total_realized_pnl,
         total_abs_pnl,
         n_sells,
         n_buys
  FROM strategy.strategy_results
  WHERE seq_id = {{seq_id}}
