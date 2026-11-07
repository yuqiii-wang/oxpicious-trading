-- The FULL rule rows behind the matched PORTFOLIO rules (the same shape as
-- signal_rules.sql): the composite orchestrating cells at the pair codes
-- UNION the member ids expanded (the mapped registered rsi cells riding
-- the is_in_portfolio TRUE tier), each cell's latest performance snapshot
-- laterally joined (perf_* columns; the service folds them back into
-- rule_params). Stored identity columns are SMALLINT label codes — decoded
-- back to the labels the UI speaks.
-- Serves GET /api/live-data/trading-signals/portfolio.
-- args:
--   sec_type_decode: fragment — labelDecodeSql("sec_type", "r.sec_type")
--   signal_type_decode: fragment — labelDecodeSql("signal_type", "r.signal_type")
--   signal_sub_type_decode: fragment — labelDecodeSql("signal_sub_type", "r.signal_sub_type")
--   trend_decode: fragment — labelDecodeSql("trend", "r.trend")
--   regime_decode: fragment — labelDecodeSql("regime", "r.regime_state")
--   action_decode: fragment — labelDecodeSql("action", "r.action")
--   perf_join: fragment — the LATEST_PERFORMANCE_JOIN lateral fragment (alias `perf`)
--   sec_type_code: fragment — LABEL_CODE.sec_type[secType]
--   codes: value text[] — the matched portfolio codes
--   rule_ids: value bigint[] — all matched portfolios' member rule ids
SELECT r.code,
            {{sec_type_decode}} AS sec_type,
            {{signal_type_decode}} AS signal_type,
            {{signal_sub_type_decode}} AS signal_sub_type,
            {{trend_decode}} AS trend,
            {{regime_decode}} AS regime_state,
            {{action_decode}} AS action,
            r.signal_rule_id::float8 AS signal_rule_id,
            r.is_valid,
            r.is_in_portfolio,
            r.rule_entry,
            r.rule_params,
            perf.perf_date,
            perf.perf_n_episodes, perf.perf_dir_ave_pct,
            perf.perf_dir_std_pct, perf.perf_t_stat,
            perf.perf_hit_rate,
            COALESCE(d.document, '{}'::jsonb) AS document,
            to_char(r.created_at, 'YYYY-MM-DD HH24:MI') AS created_at
     FROM analysis_signals.signal_rules r
     LEFT JOIN static.signal_rules_docs d
            ON d.signal_type = r.signal_type{{perf_join}}
     WHERE r.sec_type = {{sec_type_code}}
       AND (r.code = ANY({{codes}}::text[])
            OR r.signal_rule_id = ANY({{rule_ids}}::bigint[]))
     ORDER BY r.code, r.signal_type, r.signal_sub_type, r.trend,
              r.regime_state, r.action
