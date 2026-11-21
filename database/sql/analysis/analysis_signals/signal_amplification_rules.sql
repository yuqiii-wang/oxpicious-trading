-- ============================================================================
--  Table: analysis_signals.signal_amplification_rules
--
--  SIGNAL AMPLIFICATION RULES (2026-11-18; the 2026-11-20 two-level
--  amplifier axis) — the BINDING CONFIG tier of the signal-amplification
--  pair: one row per (code, sec_type, signal_type) whose rule_params
--  JSONB states WHICH amplifiers amplify that signal family on that
--  code, at what action side, base strength and depth scale — plus the
--  binding's MEASURED evidence (does an amplifier day actually improve
--  the bound family's forward response on this code's own history?).
--
--  The pair:
--    - signal_amplification_rules (this table): the per-(code,
--      signal_type) JSONB config — constant-only, wiped + reinserted
--      per run (the signal_rules convention).
--    - signal_amplification_history: the ORIGINAL RECORD KEEPER — one
--      row per amplifier DAY (the two-level amplifier_type + sub_type
--      axis) with the day's anatomy facts and the per-binding
--      amplification results.
--
--  rule_params JSONB GRAMMAR (engine-generated, never hand-typed):
--    {
--      "amplifiers": [                       -- the bound amplifier set
--        {"amplifier_type": 0,               -- the PARENT TYPE, static.label_dim 'signal_amplifier_type' code (0 ohlcv / 1 margin)
--         "sub_type": 0,                     -- the specific pattern: 'signal_amplifier' code when ohlcv,
--                                             --   'signal_amplifier_margin' code when margin
--         "action": 0,                       -- the side amplified (0 buy / 1 sell)
--         "base_bp": 150,                    -- the amplifier's base strength, bp
--         "depth_factor": 0.5,               -- confidence: base * (1 + factor * min(depth, cap))
--         "depth_cap": 2,
--         "bar_def": {...}},                 -- the amplifier's constant bars (per sub type)
--        ...
--      ],
--      "cap_bp": 600,                        -- the |amplify_bp| cap
--      "evidence": {                         -- measured per amplifier, nested type -> sub (str keys)
--        "0": {"0": {"n": 12,                -- amplifier days with a bound-family signal history response
--               "base_dir_ave_pct": 1.2,     -- the bound family's own forward blend mean on this code
--               "amp_dir_ave_pct": 2.1,      -- the mean on AMPLIFIER days only
--               "delta_pct": 0.9,            -- amp - base (the amplification claim)
--               "hit_pct": 66.7,             -- sign-correct share on amplifier days
--               "is_valid": true}}}          -- n >= AMP_EVIDENCE_MIN_N and delta sign-correct
--    }
--
--  is_valid = the CONFIG-level verdict: at least one bound amplifier's
--  evidence is_valid (an unproven config still ships its rows — the
--  amplification is observational until measured).
--
--  Populated by python -m analysis.analysis_signals.signal_amplification;
--  --force deletes the sec_type's rows and re-emits.
-- ============================================================================
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: The signal amplification binding config — per code and signal family, which amplifiers (the two-level axis: ohlcv K-line anatomy sub types or margin 融资余额 level sub types) amplify that family's signals, their base strength, depth scale and the measured evidence that the amplifier days improve the family's forward response.
-- tags: signal amplification, 信号放大, amplifier, 放大器, amplifier type, 放大器类型, sub type, 子类型, binding, 绑定, ohlcv anatomy, K线形态, margin, 融资融券, 融资余额, volume, 量能, config, 配置

DROP TABLE IF EXISTS analysis_signals.signal_amplification_rules CASCADE;

CREATE TABLE analysis_signals.signal_amplification_rules (
    code            TEXT          NOT NULL,  -- ticker (index "000001" / etf "510050" / stock)
    sec_type        SMALLINT      NOT NULL,  -- label code (static.label_dim 'sec_type'): 0=etf 1=index 2=stock
    signal_type     SMALLINT      NOT NULL,  -- the AMPLIFIED family, label code (static.label_dim 'signal_type'): 0=mov_rsi 1=bollinger ...
    rule_params     JSONB         NOT NULL,  -- the binding config — grammar documented in this file's header: amplifiers[] (amplifier_type / action / base_bp / depth_factor / depth_cap / bar_def) + cap_bp + evidence (per amplifier: n / base_dir_ave_pct / amp_dir_ave_pct / delta_pct / hit_pct / is_valid)
    is_valid        BOOLEAN       NOT NULL DEFAULT FALSE,  -- config-level verdict: at least one bound amplifier's measured evidence is_valid (n >= AMP_EVIDENCE_MIN_N and a sign-correct delta)
    rule_entry      TEXT          NOT NULL,  -- the writer class (analysis.analysis_signals.signal_amplification.amplifiers.AmplifierEngine)
    created_at      TIMESTAMP     NOT NULL DEFAULT NOW(),

    CONSTRAINT pk_signal_amplification_rules PRIMARY KEY (code, sec_type, signal_type),
    CONSTRAINT chk_signal_amplification_rules_sec_type CHECK (sec_type BETWEEN 0 AND 2)
) PARTITION BY HASH (code);

SELECT public.create_hash_partitions('analysis_signals', 'signal_amplification_rules', 16);

-- Family-first lookup (the UI's per-family rules view).
CREATE INDEX IF NOT EXISTS idx_signal_amplification_rules_family
    ON analysis_signals.signal_amplification_rules (sec_type, signal_type);

COMMENT ON TABLE  analysis_signals.signal_amplification_rules IS 'Signal AMPLIFICATION RULES: one row per (code, sec_type, signal_type) — the binding config stating which amplifiers amplify that signal family on that code (rule_params JSONB: amplifiers[] with the two-level amplifier_type (parent: signal_amplifier_type) + sub_type (signal_amplifier when ohlcv / signal_amplifier_margin when margin) + action/base_bp/depth scale/bar_def + cap_bp + the measured per-amplifier evidence). Constant-only, wiped + reinserted per run. The event records live in signal_amplification_history (the original record keeper). Populated by python -m analysis.analysis_signals.signal_amplification; --force deletes the sec_type''s rows and re-emits.';
COMMENT ON COLUMN analysis_signals.signal_amplification_rules.signal_type IS 'The AMPLIFIED signal family as its static.label_dim (''signal_type'') domain SMALLINT code — the binding TARGET (the family whose signals the amplifiers strengthen/discount), not an amplifier code.';
COMMENT ON COLUMN analysis_signals.signal_amplification_rules.rule_params IS 'The binding config (grammar in this file''s header): amplifiers[] — each {amplifier_type (the PARENT TYPE, static.label_dim ''signal_amplifier_type'' code: 0 ohlcv / 1 margin), sub_type (the specific pattern: ''signal_amplifier'' code when ohlcv, ''signal_amplifier_margin'' code when margin), action (the side amplified: 0 buy / 1 sell), base_bp, depth_factor, depth_cap, bar_def (the amplifier''s constant bars)}; cap_bp — the |amplify_bp| cap; evidence — nested {str(amplifier_type): {str(sub_type): the measured {n, base_dir_ave_pct, amp_dir_ave_pct, delta_pct, hit_pct, is_valid}}} (amplifier days vs the family''s own base on this code''s history). Engine-generated, never hand-typed.';
COMMENT ON COLUMN analysis_signals.signal_amplification_rules.is_valid IS 'TRUE when at least one bound amplifier''s evidence block is valid (n >= the evidence floor AND the amplifier-day forward mean improves on the family''s own base with the claimed sign). FALSE = observational config (rows still ship — the amplification is unproven on this code, not disproven).';
