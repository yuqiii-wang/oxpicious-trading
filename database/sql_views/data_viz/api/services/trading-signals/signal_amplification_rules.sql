-- ONE code's signal AMPLIFICATION rules (the binding-config tier behind
-- the Signal Amplification page's rules panel): one row per (code,
-- sec_type, signal_type) — which OHLCV-anatomy amplifiers amplify that
-- family on this code (rule_params JSONB: amplifiers[] + cap_bp +
-- the measured per-amplifier evidence). Stored identity columns are
-- SMALLINT label codes — decoded back to the labels the UI speaks.
-- Serves GET /api/live-data/trading-signals/amplification-rules.
-- args:
--   code: value text — the code whose binding configs to list
--   sec_type_decode: fragment — labelDecodeSql("sec_type", "r.sec_type")
--   signal_type_decode: fragment — labelDecodeSql("signal_type", "r.signal_type")
--   sec_type_code: fragment — LABEL_CODE.sec_type[secType]
SELECT r.code,
            {{sec_type_decode}} AS sec_type,
            {{signal_type_decode}} AS signal_type,
            r.rule_params,
            r.is_valid,
            r.rule_entry,
            to_char(r.created_at, 'YYYY-MM-DD HH24:MI') AS created_at
     FROM analysis_signals.signal_amplification_rules r
     WHERE r.sec_type = {{sec_type_code}} AND r.code = {{code}}
     ORDER BY r.signal_type
