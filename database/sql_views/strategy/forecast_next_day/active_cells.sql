-- The forecast module's ACTIVE RULE CELLS: every analysis_signals.
-- signal_rules row of the SCENARIO-EVALUABLE families (the close-
-- derivivable set — mov_rsi / bollinger / momentum / volatility /
-- ma_cross gaps / macd) for the run's codes, with each cell's own
-- action, (trend, regime) cell key, bar definition (rule_params.
-- bar_def) and confidence scale (rule_params.confidence), plus the
-- confidence BASE + starvation scale from the LATEST performance
-- snapshot at-or-before the anchor date (the live tier's
-- ActiveSignalStore.fetch_active_signals contract, widened to the
-- forecast families). wait rows excluded (observational, never picked
-- up by a strategy); is_in_portfolio tier rows excluded (portfolio
-- source layers never fire standalone). $1 binds the as-of date FIRST
-- (it appears inside the LATERAL join, before the WHERE clause).
-- args:
--   signals_table: fragment — analysis_signals.signal_rules
--   perf_table: fragment — analysis_signals.history_signal_performance
--   as_of: value date or None — bound the confidence base snapshot at-or-before
--   sec_type: value smallint — the sec_type's SEC_TYPE_CODE
--   codes: value text[] — the run's codes
--   signal_type_codes: value int[] — the scenario-evaluable families' codes
--   wait_code: fragment — the wait ACTION_CODE inline literal
SELECT s.code, s.sec_type, s.signal_type, s.signal_sub_type, s.action,
       s.trend, s.regime_state,
       s.rule_params->'bar_def'    AS bar_def,
       s.rule_params->'confidence' AS confidence,
       s.rule_params->>'window'    AS window,
       perf.base_pct, perf.sample_scale
FROM {{signals_table}} s
LEFT JOIN LATERAL (
    SELECT p.dir_ave_pct AS base_pct,
           LEAST(p.n_episodes::float8 / NULLIF(
               (s.rule_params->'gate'->>'sample_floor')::float8, 0),
               1.0) AS sample_scale
    FROM {{perf_table}} p
    WHERE p.code = s.code AND p.sec_type = s.sec_type
      AND p.signal_type = s.signal_type
      AND p.signal_sub_type = s.signal_sub_type
      AND p.trend = s.trend AND p.regime_state = s.regime_state
      AND p.action = s.action
      AND ({{as_of}}::date IS NULL OR p.date <= {{as_of}}::date)
    ORDER BY p.date DESC LIMIT 1
) perf ON TRUE
WHERE s.sec_type = {{sec_type}}
  AND s.code = ANY({{codes}})
  AND s.signal_type = ANY({{signal_type_codes}})
  AND s.action <> {{wait_code}}
  AND NOT s.is_in_portfolio
  AND s.is_valid
ORDER BY s.code, s.signal_type, s.signal_sub_type, s.trend,
         s.regime_state, s.action
