-- Risk metrics — all per-period gain/loss rows of ONE risk seq
-- (strategy.strategy_risk_period, ordered by period type then value).
-- Serves GET /api/strategy/singleton/risks.
-- args:
--   sec_type: value text — 'index' | 'etf' | 'stock'
--   code: value text — the traded security code
--   seq_id: value int — the strategy_identity run to list periods for
  SELECT p.seq_id, p.code, p.period_type, p.period_value,
         p.n_sells, p.n_buys, p.realized_pnl, p.ft_amplified_pnl, p.unrealized_pnl,
         p.max_loss_unrealized_pnl, p.max_gain_unrealized_pnl, p.end_unrealized_pnl,
         p.abs_pnl, p.period_share,
         p.is_concentration_hotspot, p.is_counter_trend
  FROM strategy.strategy_risk_period p
  JOIN strategy.strategy_identity s ON s.seq_id = p.seq_id
  WHERE s.sec_type = {{sec_type}} AND p.code = {{code}} AND s.seq_id = {{seq_id}}
  ORDER BY p.period_type, p.period_value