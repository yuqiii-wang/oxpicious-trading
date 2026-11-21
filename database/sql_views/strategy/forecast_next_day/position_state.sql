-- The run's codes' POSITION STATE from the nightly mixed-portfolio
-- strategy run: the latest trade_decision per code (side, exec_date,
-- position_after) plus the latest BUY decision's exec_date — the
-- forecast's SELL-pickup reading inputs (a sell event executes only
-- when a position is open past min_holding_period bars since the last
-- BUY; the trading-row distance is counted by the caller against its
-- own close ladder). The code lives on strategy_identity (trade_
-- decision carries only seq_id) — every code reference goes through
-- the identity alias. strategy_identity carries sec_type as TEXT (the
-- strategy schema's legacy convention — the label, not the code).
-- args:
--   strategy_name: value text — the portfolio strategy the reading applies to
--   sec_type_label: value text — the sec_type label ('index')
--   codes: value text[] — the run's codes
SELECT DISTINCT ON (i.code)
       i.code,
       d.side AS last_side,
       d.exec_date AS last_exec_date,
       d.position_after AS position_after,
       buys.last_buy_date
FROM strategy.trade_decision d
JOIN strategy.strategy_identity i ON i.seq_id = d.seq_id
LEFT JOIN LATERAL (
    SELECT b2.exec_date AS last_buy_date
    FROM strategy.trade_decision b2
    JOIN strategy.strategy_identity i2 ON i2.seq_id = b2.seq_id
    WHERE i2.strategy_name = {{strategy_name}}
      AND i2.sec_type = {{sec_type_label}}
      AND i2.code = i.code
      AND i2.is_active
      AND b2.side = 'BUY'
    ORDER BY b2.exec_date DESC, b2.decision_no DESC
    LIMIT 1
) buys ON TRUE
WHERE i.strategy_name = {{strategy_name}}
  AND i.sec_type = {{sec_type_label}}
  AND i.code = ANY({{codes}})
  AND i.is_active
ORDER BY i.code, d.exec_date DESC, d.decision_no DESC
