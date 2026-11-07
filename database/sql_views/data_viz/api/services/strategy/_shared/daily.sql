-- Strategy backtest — daily portfolio state from first BUY to end (one row
-- per trading day; unrealized_pnl marks the close-out P&L at that day's
-- close, normalized_mean_buy_period the qty-weighted mean buy time).
-- Consumed by services/strategy/backtest.ts.
-- args:
--   seq_id: value int — the strategy_seq id
  SELECT trade_date, unrealized_pnl, total_pnl, realized_pnl_cum,
         total_qty, position_value, normalized_mean_buy_period,
         return_rate,
         sharpe_ratio, sharpe_ratio_255d, sharpe_ratio_500d
  FROM strategy.strategy_daily
  WHERE seq_id = {{seq_id}}
  ORDER BY trade_date ASC
