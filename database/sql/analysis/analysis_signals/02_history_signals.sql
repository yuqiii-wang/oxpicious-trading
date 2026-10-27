-- ============================================================================
--  Table: analysis_signals.history_signals
--
--  history_signals — one row per TRIGGER DAY (the event record,
--  populated by REPLAYING the signal_rules over history — the
--  redesigned tier, 2026-10-14): each qualifying episode's FIRST day,
--  with the day's indicator value, the cell's prepared bar it crossed
--  (denormalized per event) and the depth-scaled confidence. The owning
--  rule cell lives in 01_signal_rules.sql — (code, sec_type,
--  signal_type, signal_sub_type, trend, regime_state, action); the
--  event's action/regime restate the cell key the day fired under.
--
--  Populated by python -m analysis.analysis_signals (incremental at
--  (code, signal_type) granularity; --force deletes the sec_type's rows
--  and re-emits everything).
-- ============================================================================

DROP TABLE IF EXISTS analysis_signals.history_signals CASCADE;

CREATE TABLE analysis_signals.history_signals (
    code            TEXT          NOT NULL,  -- ticker (etf "510050" / index "000300" / stock)
    sec_type        SMALLINT      NOT NULL,  -- label code (static.label_dim 'sec_type'): 0=etf 1=index 2=stock
    signal_type     SMALLINT      NOT NULL,  -- label code (static.label_dim 'signal_type'): 0=mov_rsi 1=bollinger 2=ma_cross 3=options_wall 4=options_vix 5=momentum — the strategy's detection family
    signal_sub_type SMALLINT      NOT NULL,  -- the strategy this event belongs to, as its static.label_dim ('signal_sub_type') THRESHOLD code (e.g. 'rsi14_2pct' / 'boll20_2p5std' / 'ma120_g5pct' / 'ow_put_025pct' / 'ovix_spike')
    is_ohlcv_boosted BOOLEAN      NOT NULL DEFAULT FALSE,  -- PK member: the OHLCV-boost VARIANT axis — SHARED variant emitters re-activated it 2026-10-26 (user-directed; the twin era's key kept): TRUE = the event replays the TURNOVER-CLIMAX-confirmed variant of its rule cell (the shared variants gate, amt_climax >= HEAVY — bollinger's band breaches, rf_break's deep legs), a SEPARATE row from the unconstrained variant's event at the same (code, sub, date, time); FALSE = the plain population of record (the trust-weighted measurement never gates)
    date            DATE          NOT NULL,  -- the trigger day — each qualifying episode's FIRST day (the moment the signal becomes observable)
    time            TIME          NOT NULL,  -- the bar time of the recorded breach (history rows: 15:00:00 day close)

    action          SMALLINT      NOT NULL,  -- label code (static.label_dim 'action'): 0 buy (downward breach) | 1 sell (upward breach) | 2 wait (observational only since 2026-10-19 — the ungated VRP-declined twin; the sample-STARVED wait replay was retired: an under-floor cell's triggers now record its REAL action at the starvation-scaled confidence)
    signal          NUMERIC(16,6) NOT NULL,  -- the day's indicator value that crossed the bar (mov_rsi: the RSI value; bollinger: the σ-depth z; ma_cross: the price-vs-MA gap; options: the wall distance / corr value)
    signal_reference NUMERIC(14,6) NOT NULL, -- the bar crossed — the cell's prepared rule_params bar, denormalized per event
    confidence      INTEGER       NOT NULL DEFAULT 100,  -- the SCALED breach confidence in basis points (ROUND(10000 x base x scale(depth) x sample_scale) from the cell's rule_params — sample_scale = min(n/floor, 1) the starvation haircut, 1.0 for fully-sampled cells)
    is_day_close_trigger BOOLEAN  NOT NULL DEFAULT TRUE,  -- TRUE = history row recorded at the day close (time 15:00:00)
    regime_state    SMALLINT      NOT NULL DEFAULT 0,  -- label code (static.label_dim 'regime'): the trigger day's market regime (0 calm / 1 hot / 2 panic / 3 quiet; 4 = the regime-free families' POOLED stamp)
    is_typical      BOOLEAN,  -- the trigger day's episode-PASS attribution where the family defines one (the uninterrupted-run / isolated-touch split); NULL = the family does not classify passes
    is_in_portfolio BOOLEAN  NOT NULL DEFAULT FALSE,  -- PK member (2026-10-23, user-directed): the event's rule cell belongs to the PORTFOLIO tier — sourced by a portfolio family's source layer (the hedge components' mov_rsi / rf_break rows riding the SAME family vocabularies at the market + leg codes) — mirroring signal_rules.is_in_portfolio so a sourced cell's events are SEPARATE rows from the standalone families' events at the same (code, sub, date, time); the portfolio_history_signals.cell_signals linkage keys carry it
    is_pe_boosted   BOOLEAN  NOT NULL DEFAULT FALSE,  -- PK member (2026-10-26, user-directed, the is_ohlcv_boosted/is_in_portfolio PK-member precedent): mirrors signal_rules.is_pe_boosted — the event replays the PE-CONDITIONED variant of its rule cell (a SEPARATE row from the unconstrained variant's event at the same (code, sub, date, time)); FALSE = the plain PE-free population (every non-PE family)

    created_at      TIMESTAMP     NOT NULL DEFAULT NOW(),

    CONSTRAINT pk_history_signals PRIMARY KEY (code, sec_type, signal_type, signal_sub_type, is_ohlcv_boosted, date, time, is_in_portfolio, is_pe_boosted),
    CONSTRAINT chk_history_signals_action CHECK (action BETWEEN 0 AND 2),
    CONSTRAINT chk_history_signals_regime CHECK (regime_state BETWEEN 0 AND 4),
    CONSTRAINT chk_history_signals_sec_type CHECK (sec_type BETWEEN 0 AND 2)
) PARTITION BY HASH (code);

SELECT public.create_hash_partitions('analysis_signals', 'history_signals', 16);

-- Date-first lookup (UI / "signals on day X for sec_type Y").
CREATE INDEX IF NOT EXISTS idx_history_signals_date
    ON analysis_signals.history_signals (sec_type, signal_type, date);

-- ----------------------------------------------------------------------------
--  Data-quality gates (shared helpers, see 00_partition_utils.sql): the
--  history confidence in integer basis points, in [0, 10000].
-- ----------------------------------------------------------------------------
SELECT public.ensure_check_constraint(
    'analysis_signals.history_signals',
    'chk_history_signals_confidence',
    $chk$confidence BETWEEN 0 AND 10000$chk$);

SELECT public.validate_pending_checks('analysis_signals');

COMMENT ON TABLE analysis_signals.history_signals IS 'History SIGNAL EVENTS: one row per qualifying trigger day, populated by REPLAYING analysis_signals.signal_rules over history — each qualifying episode''s FIRST day with the day''s indicator value, the crossed cell bar and the depth-scaled confidence. Populated by python -m analysis.analysis_signals; --force deletes the sec_type''s rows and re-emits.';
COMMENT ON COLUMN analysis_signals.history_signals.signal_reference IS 'The bar crossed — the firing rule cell''s prepared bar from signal_rules.rule_params, denormalized per event (per-code percentile / fixed sigma / gap pct per family semantics).';
COMMENT ON COLUMN analysis_signals.history_signals.confidence IS 'The SCALED breach confidence on the INTEGER basis-point scale: ROUND(10000 x base x scale(depth) x sample_scale) — the cell''s measured on-bar base amplified over the breach depth per its rule_params scale and multiplied by the starvation haircut min(n/floor, 1) (2026-10-19; 1.0 for fully-sampled cells); [0, 10000].';
COMMENT ON COLUMN analysis_signals.history_signals.action IS 'The event''s action as its static.label_dim (''action'') domain SMALLINT code: 0 buy (downward breach — reversal up), 1 sell (upward breach — exit timing), 2 wait (observational only since 2026-10-19 — options_wall''s ungated VRP-declined breach twin. The 2026-10-16 sample-STARVED wait replay was retired: a cell under its sample floor now records its REAL action at the starvation-scaled confidence (min(n/floor, 1) x the depth scale — the monotone guarantee kept as a REDUCED-CONFIDENCE trading record, never a not-trading-yet row).';
COMMENT ON COLUMN analysis_signals.history_signals.regime_state IS 'The trigger day''s market regime as its static.label_dim (''regime'') domain SMALLINT code (0 calm / 1 hot / 2 panic / 3 quiet; 4 = POOLED). Recorded context: mov_rsi/bollinger events fire under their own regime split cell; the regime-free families (ma_cross, options_wall, options_vix) stamp 4.';
COMMENT ON COLUMN analysis_signals.history_signals.is_in_portfolio IS 'The event''s rule cell belongs to the PORTFOLIO tier (PK member since 2026-10-23, user-directed): TRUE = the event replays a cell SOURCED by a portfolio family''s source layer (the hedge components'' mov_rsi / rf_break rows riding the same family vocabularies at the market + leg-industry codes — the coordinated CELL signals the portfolio events'' cell_signals linkage fetches them through), FALSE = the standalone families'' events and the orchestrating composite events. Mirrors signal_rules.is_in_portfolio so a sourced cell''s episode-first records are SEPARATE rows from the standalone families'' events at the same (code, signal_sub_type, date, time) — the family runs and the --force purges partition on it exactly like the rules wipe scopes.';
