-- ----------------------------------------------------------------------------
--  Signal RULES — the family knowledge + the self-adaptive params tier
--  (the redesign's centerpiece, 2026-10-14: signal_thresholds /
--  signal_curves were RETIRED — this table replaces them).
--
--  ONE row per rule CELL: (code, sec_type, signal_type, signal_sub_type,
--  trend, regime_state, action). The SAME cell key selects which rule
--  fires on a given day when history is replayed (the day's own trend /
--  regime choose the row), which is why rule_params are prepared PER PK
--  — different codes, and the same code in different trends, measure
--  asymmetric populations and get DIFFERENT prepared parameters.
--
--  Columns:
--    - is_valid the per-cell GATE VERDICT (2026-10-16): TRUE only when
--                 the cell's measured FUTURE RETURNS cleared its side's
--                 dir_ave bar at the sample floor (buy >= 0.25% @ n >= 3
--                 / sell >= 0% @ n >= 2) — the same boolean as
--                 rule_params.gate.passed, denormalized for SQL filters;
--                 negative-future-return evidence is DISCOURAGED: it can
--                 never carry is_valid (measured-out cells do not emit at
--                 all). FALSE = observational (the starved wait
--                 watchlist / the ungated VRP twin) — never trades, never
--                 fires live.
--    - rule_entry the engine ENTRY that runs the rule — the RuleEntry
--                 subclass name in
--                 analysis/analysis_signals/engines/signal_families/
--                 (e.g. "MaCrossGdepthEntry"); the class implements the
--                 detection logic, this column is how a row finds its
--                 runner.
--    - rule_params the SELF-ADAPTIVE prepared parameters (JSONB),
--                 measured by the engine on the code's own trailing
--                 history for THIS cell: the breach bar (per-code
--                 percentile / fixed sigma / gap pct per family
--                 semantics), the episode merge K, the gate numbers the
--                 cell passed, the measured evidence (n_episodes,
--                 dir_ave, hit_rate) and the confidence scale. Generated
--                 by the engine, never hand-typed.
--
--  The FAMILY KNOWLEDGE document (name / description / condition /
--  sources / principles / the sub_signals map) is NOT stored here — it
--  is byte-identical within a family, so it lives ONE row per family
--  in static.analysis_signal_docs, seeded from
--  database/sql/static/analysis_signal_docs/ (dropped from this table
--  2026-10-15); the rules API joins it per family at the read
--  boundary.
--
--  A rules row is current-state (created_at, no window columns): the
--  engine re-measures and upserts the cell each run.
-- ----------------------------------------------------------------------------
DROP TABLE IF EXISTS analysis_signals.signal_rules CASCADE;

CREATE TABLE analysis_signals.signal_rules (
    code            TEXT         NOT NULL,  -- ticker (etf "510050" / index "000300" / stock)
    sec_type        SMALLINT     NOT NULL,  -- label code (static.label_dim 'sec_type'): 0=etf 1=index 2=stock
    signal_type     SMALLINT     NOT NULL,  -- label code (static.label_dim 'signal_type'): 0=mov_rsi 1=bollinger 2=ma_cross 3=options_wall 4=options_vix 5=momentum — the detection family
    signal_sub_type SMALLINT     NOT NULL,  -- the family config grid as its static.label_dim ('signal_sub_type') SMALLINT code (e.g. 401 'rsi14_2pct' / 801 'boll20_2p5std' / 2401 'ma120_g5pct' / 4100 'ow_put_025pct' / 4300 'ovix_spike')
    trend           SMALLINT     NOT NULL,  -- label code (static.label_dim 'trend'): 0 top | 1 bottom | 2 rise | 3 fall | 4 all (POOLED — the sparse non-trend-split families' stamp) | 5 unknown — the code's own trend classification in force for the cell (rise/fall = the RSI re-entry episodes since 2026-10-15)
    regime_state    SMALLINT     NOT NULL DEFAULT 0,  -- label code (static.label_dim 'regime'): 0 calm / 1 hot / 2 panic / 3 quiet / 4 all (pooled — regime-free families stamp 4)
    action          SMALLINT     NOT NULL,  -- label code (static.label_dim 'action'): 0 buy | 1 sell | 2 wait — the rule's conclusion (2 = not trading yet, renamed from no_action 2026-10-16: the VRP-declined twin, or the sample-STARVED cell whose episodes sit below the floor — the SELECTIVE rules watchlist carries only floor-blocked imminent cells (n = floor-1 at the bar); never traded; gate.starved + gate.intended_action in rule_params)

    is_valid        BOOLEAN      NOT NULL DEFAULT false,  -- the per-cell GATE VERDICT (2026-10-16): TRUE iff the cell's measured future returns cleared the side's dir_ave bar at the sample floor (buy >= 0.25% @ n >= 3 / sell >= 0% @ n >= 2 — rule_params.gate.passed denormalized); FALSE = observational (the starved wait watchlist / the ungated VRP twin) — negative-future-return evidence is discouraged, never valid
    rule_entry      TEXT         NOT NULL,  -- the engine entry that runs the rule — the RuleEntry subclass name under analysis/analysis_signals/engines/signal_families/
    rule_params     JSONB        NOT NULL,  -- the self-adaptive prepared parameters the entry measured on the code's own history for THIS cell (bar, K, gate, evidence, scale)
    ohlcv_boost_conf FLOAT8,     -- the ohlcv boost's trust-weighted confidence BASE for the cell (pct, the same scale as rule_params.confidence.base_pct): sum(w*y)/sum(w) over the cell's measurable trigger days, w = the day-0 anatomy trust weight of the side's passing derivation; NULL = the side boosted nothing (ship gates failed / family not opted in) — an honest absence, never a gate

    created_at      TIMESTAMP    NOT NULL DEFAULT NOW(),

    CONSTRAINT pk_signal_rules PRIMARY KEY (code, sec_type, signal_type, signal_sub_type, trend, regime_state, action),
    CONSTRAINT chk_signal_rules_action CHECK (action BETWEEN 0 AND 2),  -- 2 = wait
    CONSTRAINT chk_signal_rules_trend CHECK (trend BETWEEN 0 AND 5),  -- 5 = unknown (the RSI re-entry classifier's unclaimed bucket, 2026-10-15)
    CONSTRAINT chk_signal_rules_regime CHECK (regime_state BETWEEN 0 AND 4),  -- 4 = all (POOLED)
    CONSTRAINT chk_signal_rules_sec_type CHECK (sec_type BETWEEN 0 AND 2)
) PARTITION BY HASH (code);

SELECT public.create_hash_partitions('analysis_signals', 'signal_rules', 16);

-- Family-first lookup (which cells exist for a family's universe).
CREATE INDEX IF NOT EXISTS idx_signal_rules_family
    ON analysis_signals.signal_rules (sec_type, signal_type);

COMMENT ON TABLE analysis_signals.signal_rules IS 'Signal rules — the family knowledge + self-adaptive params tier (replaces the retired signal_thresholds / signal_curves, 2026-10-14). One row per (code, config, trend, regime, action) cell; history_signals is populated by REPLAYING these rules (the day''s own trend/regime select the cell). rule_entry names the RuleEntry engine subclass that runs the row; rule_params are the parameters that engine measured on the code''s own history for that cell — they differ per PK by design (trend asymmetry). The family knowledge document lives ONLY in the repo''s static template JSONs (engines/signal_families/<family>/static/), served by the API from those files.';
COMMENT ON COLUMN analysis_signals.signal_rules.is_valid IS 'The per-cell GATE VERDICT (2026-10-16): TRUE iff the cell''s measured future returns (the sign-aligned dir_ave blend) cleared its side''s dir_ave bar at the sample floor — buy >= 0.25% @ n >= 3, sell >= 0% @ n >= 2 (rule_params.gate.passed denormalized for SQL filtering). FALSE = observational (the starved wait watchlist / the ungated VRP twin): negative-future-return evidence is DISCOURAGED — it never carries is_valid (measured-out cells do not emit at all), never trades and never fires live.';
COMMENT ON COLUMN analysis_signals.signal_rules.rule_entry IS 'The engine entry that runs the rule — the RuleEntry subclass name under analysis/analysis_signals/engines/signal_families/ (e.g. "MaCrossGdepthEntry", "RsiTailEntry"). The class implements the detection logic; this column is how a row finds its runner.';
COMMENT ON COLUMN analysis_signals.signal_rules.rule_params IS 'The self-adaptive prepared parameters (JSONB) the entry measured on the code''s own trailing history for THIS cell: the breach bar (per-code percentile / fixed sigma / gap pct per family semantics), episode merge K, the gate numbers, the measured evidence (n_episodes, dir_ave, hit_rate) and the confidence scale. Engine-generated, never hand-typed.';
COMMENT ON COLUMN analysis_signals.signal_rules.ohlcv_boost_conf IS 'The ohlcv boost''s trust-weighted confidence BASE for the cell (pct — the same scale as rule_params.confidence.base_pct): sum(w*y)/sum(w) over the cell''s measurable trigger days, w = the day-0 anatomy trust weight (the side''s self-adaptive derivation, ship-gated). NULL = the side boosted nothing (gates failed / family not opted in) — an honest absence. The boost is a trust-weighted MEASUREMENT, never a gate (2026-10-16: replaced the boost-twin history rows — only confidence is tuned, and it lives here).';
