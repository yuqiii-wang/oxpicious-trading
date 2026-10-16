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
    is_ohlcv_boosted BOOLEAN      NOT NULL DEFAULT FALSE,  -- RETIRED variant marker (PK member, kept for the key's shape): the ohlcv-boost twin rows were retired 2026-10-16 — the boost's tuned confidence lives on signal_rules.ohlcv_boost_conf; the column reads FALSE on every row
    date            DATE          NOT NULL,  -- the trigger day — each qualifying episode's FIRST day (the moment the signal becomes observable)
    time            TIME          NOT NULL,  -- the bar time of the recorded breach (history rows: 15:00:00 day close)

    action          SMALLINT      NOT NULL,  -- label code (static.label_dim 'action'): 0 buy (downward breach) | 1 sell (upward breach) | 2 wait (the condition fired but the rule is not trading yet, renamed from no_action 2026-10-16 — the VRP-declined twin, or the cell is sample-STARVED below its floor: the extreme-event record; EVERY starved cell's triggers replay here while the rules watchlist row stays selective; graduates to the real action in place once the gate passes)
    signal          NUMERIC(16,6) NOT NULL,  -- the day's indicator value that crossed the bar (mov_rsi: the RSI value; bollinger: the σ-depth z; ma_cross: the price-vs-MA gap; options: the wall distance / corr value)
    signal_reference NUMERIC(14,6) NOT NULL, -- the bar crossed — the cell's prepared rule_params bar, denormalized per event
    confidence      INTEGER       NOT NULL DEFAULT 100,  -- the SCALED breach confidence in basis points (ROUND(10000 x base x scale(depth)) from the cell's rule_params)
    is_day_close_trigger BOOLEAN  NOT NULL DEFAULT TRUE,  -- TRUE = history row recorded at the day close (time 15:00:00)
    regime_state    SMALLINT      NOT NULL DEFAULT 0,  -- label code (static.label_dim 'regime'): the trigger day's market regime (0 calm / 1 hot / 2 panic / 3 quiet; 4 = the regime-free families' POOLED stamp)
    is_typical      BOOLEAN,  -- the trigger day's episode-PASS attribution where the family defines one (the uninterrupted-run / isolated-touch split); NULL = the family does not classify passes

    created_at      TIMESTAMP     NOT NULL DEFAULT NOW(),

    CONSTRAINT pk_history_signals PRIMARY KEY (code, sec_type, signal_type, signal_sub_type, is_ohlcv_boosted, date, time),
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
COMMENT ON COLUMN analysis_signals.history_signals.confidence IS 'The SCALED breach confidence on the INTEGER basis-point scale: ROUND(10000 x base x scale(depth)) — the cell''s measured on-bar base amplified over the breach depth per its rule_params scale; [0, 10000].';
COMMENT ON COLUMN analysis_signals.history_signals.action IS 'The event''s action as its static.label_dim (''action'') domain SMALLINT code: 0 buy (downward breach — reversal up), 1 sell (upward breach — exit timing), 2 wait (RENAMED from no_action 2026-10-16 — the condition fired but the rule is not trading yet: e.g. options_wall''s VRP-declined breach twin, or the cell is sample-STARVED — episodes below the sample floor, the extreme-event record since 2026-10-16 and the monotone guarantee that a deepest-claim breach never vanishes because its cell is under-floored; every starved cell''s triggers replay as wait events while the signal_rules watchlist row stays selective (floor-blocked imminent cells only); the row upserts to the real action once n clears the floor and the gate passes).';
COMMENT ON COLUMN analysis_signals.history_signals.regime_state IS 'The trigger day''s market regime as its static.label_dim (''regime'') domain SMALLINT code (0 calm / 1 hot / 2 panic / 3 quiet; 4 = POOLED). Recorded context: mov_rsi/bollinger events fire under their own regime split cell; the regime-free families (ma_cross, options_wall, options_vix) stamp 4.';
