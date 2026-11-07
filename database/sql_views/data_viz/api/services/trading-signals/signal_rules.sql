-- ONE code's rule cells (the rules tier behind the Recent Movements code
-- trend's rules panel): every (sub signal, trend, regime, action) cell the
-- engines measured and shipped, one row each, with the family knowledge
-- document joined from static.signal_rules_docs and the cell's LATEST
-- history_signal_performance snapshot laterally joined (perf_* columns —
-- the measured half of the 2026-10-19 split; the service folds it back
-- into rule_params). Stored identity columns are SMALLINT label codes —
-- decoded back to the labels the UI speaks.
-- Serves GET /api/live-data/trading-signals/rules.
-- args:
--   code: value text — the code whose rule cells to list
--   sec_type_decode: fragment — labelDecodeSql("sec_type", "r.sec_type")
--   signal_type_decode: fragment — labelDecodeSql("signal_type", "r.signal_type")
--   signal_sub_type_decode: fragment — labelDecodeSql("signal_sub_type", "r.signal_sub_type")
--   trend_decode: fragment — labelDecodeSql("trend", "r.trend")
--   regime_decode: fragment — labelDecodeSql("regime", "r.regime_state")
--   action_decode: fragment — labelDecodeSql("action", "r.action")
--   perf_join: fragment — the LATEST_PERFORMANCE_JOIN lateral fragment (alias `perf`)
--   sec_type_code: fragment — LABEL_CODE.sec_type[secType]
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
     WHERE r.sec_type = {{sec_type_code}} AND r.code = {{code}}
     ORDER BY r.signal_type, r.signal_sub_type, r.trend,
              r.regime_state, r.action
