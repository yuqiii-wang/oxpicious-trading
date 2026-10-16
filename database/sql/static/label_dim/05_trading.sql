-- ============================================================================
--  static.label_dim seed — TRADING vocabularies
--
--  Order/trade intent at the strategy layer. Owning table:
--  strategy.trade_decision (trade_side) — PENDING: vocabulary
--  registered, the column is STILL TEXT until table + writers convert.
-- ============================================================================

INSERT INTO static.label_dim (domain, code, label, sort_order) VALUES
    -- trade_side: strategy.trade_decision (PENDING)
    ('trade_side', 0, 'BUY',  0),
    ('trade_side', 1, 'SELL', 1);
