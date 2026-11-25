-- The Strategy → Forecast page read: strategy.forecast_next_day rows
-- for a sec_type, bounded to one code when given and to the LATEST
-- anchor date per code when no explicit anchor is given (the page's
-- default view). The stored vocabularies are SMALLINT label codes —
-- decoded here through static.label_dim (never CASE). Serves
-- GET /api/strategy/forecast.
-- args:
--   direction_decode: fragment — labelDecodeSql("forecast_direction", "f.direction")
--   signal_type_decode: fragment — labelDecodeSql("signal_type", "f.signal_type")
--   sub_type_decode: fragment — labelDecodeSql("signal_sub_type", "f.signal_sub_type")
--   action_decode: fragment — labelDecodeSql("action", "f.action")
--   trend_decode: fragment — labelDecodeSql("trend", "f.trend_state")
--   regime_decode: fragment — labelDecodeSql("regime", "f.regime_state")
--   sec_type: value smallint — the sec_type's LABEL_CODE
--   code: value text or None — bound to one code
--   anchor_date: value date or None — bound to one anchor (None = latest per code)
--   identity_table: fragment — IDENTITY_TABLE[secType] (the sec_type's
--     stats identity source, scalar-subqueried for the display name)
SELECT f.anchor_date, f.code,
       (SELECT i.name FROM {{identity_table}} i
        WHERE i.code = f.code
        ORDER BY i.date DESC LIMIT 1) AS code_name,
       {{direction_decode}} AS direction,
       f.sigma_mult, f.trigger_no, f.base_close, f.sigma_20d_pct,
       f.forecast_close, f.forecast_change_pct,
       {{signal_type_decode}} AS signal_type,
       {{sub_type_decode}} AS signal_sub_type,
       {{action_decode}} AS action,
       f.signal_value, f.signal_threshold, f.signal_excess_pct,
       f.confidence_bp,
       {{trend_decode}} AS trend_state,
       {{regime_decode}} AS regime_state,
       f.position_open, f.would_execute, f.strategy_name,
       f.updated_at
FROM strategy.forecast_next_day f
WHERE f.sec_type = {{sec_type}}
  AND ({{code}}::text IS NULL OR f.code = {{code}})
  AND (
      {{anchor_date}}::date IS NOT NULL
      OR f.anchor_date = (
          SELECT MAX(f2.anchor_date) FROM strategy.forecast_next_day f2
          WHERE f2.sec_type = f.sec_type AND f2.code = f.code
      )
  )
  AND ({{anchor_date}}::date IS NULL OR f.anchor_date = {{anchor_date}}::date)
ORDER BY f.code, f.direction, f.sigma_mult, f.trigger_no
