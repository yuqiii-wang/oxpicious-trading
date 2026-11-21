-- Risk metrics — risk-score contribution factors of ONE risk seq
-- (strategy.strategy_risk_factors, presentation-ordered via the fixed
-- component rank — array_position over the known components, unmapped
-- ones last — then sub_key).
-- Serves GET /api/strategy/singleton/risks.
-- args:
--   seq_id: value int — the strategy_identity run to list factors for
--   code: value text — the traded security code
  SELECT f.seq_id, f.code, f.component, f.label, f.sub_key,
         f.contribution, f.raw_value, f.threshold, f.ratio
  FROM strategy.strategy_risk_factors f
  WHERE f.seq_id = {{seq_id}} AND f.code = {{code}}
  ORDER BY
    array_position(
      ARRAY['realized', 'unrealized', 'streak', 'period_asymmetry', 'period_tail']::text[],
      f.component) NULLS LAST,
    f.sub_key