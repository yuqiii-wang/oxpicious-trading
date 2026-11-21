-- ============================================================================
--  static.label_dim seed — TRADING vocabularies
--
--  Order/trade intent at the strategy layer. Owning tables:
--  strategy.trade_decision (trade_side) — PENDING: vocabulary
--  registered, the column is STILL TEXT until table + writers convert;
--  strategy.forecast_next_day (forecast_direction).
-- ============================================================================

INSERT INTO static.label_dim (domain, code, label, sort_order) VALUES
    -- trade_side: strategy.trade_decision (PENDING)
    ('trade_side', 0, 'BUY',  0),
    ('trade_side', 1, 'SELL', 1);

INSERT INTO static.label_dim (domain, code, label, sort_order) VALUES
    -- forecast_direction: strategy.forecast_next_day.direction (the ±kσ
    -- next-day scenario's side of the move)
    ('forecast_direction', 0, 'down', 0),
    ('forecast_direction', 1, 'up',   1);
