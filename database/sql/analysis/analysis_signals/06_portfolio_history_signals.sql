-- ============================================================================
--  Table: analysis_signals.portfolio_history_signals  (2026-10-21)
--
--  PORTFOLIO HISTORY SIGNALS — the PORTFOLIO tier's EVENT record, the
--  history_signals counterpart at the portfolio level: ONE ROW PER
--  PORTFOLIO EVENT (the composite/pair-native rule cells' replayed
--  triggers at the portfolio codes — the hedge_pairs family's
--  orchestration layer), carrying THE EVENT DATE (the day the
--  coordinated event COMPLETED — all component witnesses alive) and
--  THE ASSOCIATION to the member CELL-LEVEL signal events that
--  triggered it:
--
--    member_signals  JSONB array — the SOURCED COMPONENT CELL EVENTS
--      the portfolio event coordinates (the sourced mov_rsi rows the
--      portfolio_signal_rules group references, resolved AT THE EVENT
--      level): [{code, signal_type, signal_sub_type, action, date,
--      signal, signal_reference, confidence}, ...]. Each member is a
--      full cell-level signal record WITH ITS OWN TRIGGER DATE — the
--      components' episodes fire on their OWN calendars, so one
--      portfolio event's members can be DIFFERENT SUB SIGNALS on
--      DIFFERENT NEARBY DAYS (the trio entry: the market's RSI
--      capitulation days ago + leg A's yesterday + leg B's today).
--      The member is the component's LATEST episode-first trigger
--      ACROSS ITS TAIL GRID at-or-before the event day — the alive
--      capitulation's ACTIVE claim (the witness is the raw tail
--      breach, which stays lit while the component sits in its deeper
--      tail; those days carve away from the shallower cell's
--      episodes, so a per-tail lookup would point at a stale episode).
--      Label codes stay SMALLINT inside the payload (the static.
--      label_dim domains) — decoded at the API boundary only. Empty
--      array = the PAIR-NATIVE events (harvest / momentum tilt /
--      wash / rho50 / reclaim / stop) whose trigger is the pair's own
--      gauge — no sourced component cells participate.
--
--  WHY A SEPARATE TABLE (not columns on history_signals): the cell
--  tier's history is per RULE CELL — its PK has no portfolio axis and
--  no place for a cross-code member association; the portfolio event
--  is a DIFFERENT grain (one row per coordinated event with its
--  members, not one row per cell trigger). The composite events still
--  ride history_signals (the cell tier's population of record, the
--  chart markers); this table ADDS the event-level view with the
--  member association the UI's expandable rows render. The component
--  cells' own history stays DROPPED from history_signals (per-code
--  info, the 2026-10-18 source-layer design) — the member records
--  here are the ONE place the triggering cell events are kept.
--
--  Lifecycle (python -m analysis.analysis_signals, the portfolio
--  families — hedge_pairs today): written in the SAME run as
--  history_signals from the same replayed rows; --force purges the
--  (sec_type, portfolio_type) tier then re-emits; incremental runs
--  upsert past the table's max date (idempotent). NOT hash-partitioned
--  (the portfolio_signal_rules precedent — a small composite-event
--  table, PK- and date-addressed reads only).
-- ============================================================================

DROP TABLE IF EXISTS analysis_signals.portfolio_history_signals CASCADE;

CREATE TABLE analysis_signals.portfolio_history_signals (
    code            TEXT          NOT NULL,  -- the PORTFOLIO code — the composite 'A~B' pair id (mirrors portfolio_signal_rules.code)
    sec_type        SMALLINT      NOT NULL,  -- label code (static.label_dim 'sec_type'): 0=etf 1=index 2=stock — the pipeline run's sec_type scope
    portfolio_type  SMALLINT      NOT NULL,  -- label code (static.label_dim 'portfolio_type'): 0 neg_corr_hedge_pair — mirrors portfolio_signal_rules.portfolio_type
    signal_type     SMALLINT      NOT NULL,  -- label code (static.label_dim 'signal_type'): the COMPOSITE rule's detection family (7 hedge_pairs / 8 hp_rsi) — the event's owning rule cell key
    signal_sub_type SMALLINT      NOT NULL,  -- label code (static.label_dim 'signal_sub_type'): the composite sub (hp_entry_legs_rsi, hp_harvest, ...)
    action          SMALLINT      NOT NULL,  -- label code (static.label_dim 'action'): 0 buy | 1 sell | 2 wait — the composite event's action
    date            DATE          NOT NULL,  -- THE EVENT DATE — the day the coordinated event completed (every component witness alive; the composite cell's trigger day)
    time            TIME          NOT NULL,  -- the bar time of the recorded event (15:00:00 day close — the history convention)

    signal          NUMERIC(16,6) NOT NULL,  -- the event day's composite value (rsi_mean / ratio_z / washdepth / rho_off per the sub)
    signal_reference NUMERIC(14,6) NOT NULL, -- the composite bar crossed — the cell's prepared bar, denormalized per event (the history_signals convention)
    confidence      INTEGER       NOT NULL DEFAULT 100,  -- the SCALED confidence in basis points (the composite cell's depth/starvation-scaled record — identical arithmetic to history_signals.confidence)
    regime_state    SMALLINT      NOT NULL DEFAULT 4,  -- label code (static.label_dim 'regime'): the event day's regime; 4 = the pooled stamp (the family pools on both axes today)
    is_typical      BOOLEAN,                 -- the composite cell's episode-pass attribution where defined; NULL = unclassified (the history_signals convention)

    member_signals  JSONB         NOT NULL DEFAULT '[]',  -- THE ASSOCIATION: the member CELL-LEVEL signal events that triggered this portfolio event — [{code, signal_type, signal_sub_type, action, date, signal, signal_reference, confidence}, ...] — the sourced component cells' OWN trigger records (different subs, different nearby days); label codes stay SMALLINT (API-boundary decode); '[]' = the pair-native events with no sourced components

    created_at      TIMESTAMP     NOT NULL DEFAULT NOW(),

    CONSTRAINT pk_portfolio_history_signals PRIMARY KEY
        (code, sec_type, portfolio_type, signal_type, signal_sub_type,
         date, time),
    CONSTRAINT chk_portfolio_history_signals_sec_type
        CHECK (sec_type BETWEEN 0 AND 2),
    CONSTRAINT chk_portfolio_history_signals_action
        CHECK (action BETWEEN 0 AND 2),
    CONSTRAINT chk_portfolio_history_signals_regime
        CHECK (regime_state BETWEEN 0 AND 4)
);

-- Date-first lookup (the portfolio history panel's event-day listing).
CREATE INDEX IF NOT EXISTS idx_portfolio_history_signals_date
    ON analysis_signals.portfolio_history_signals
    (sec_type, portfolio_type, date);

-- ----------------------------------------------------------------------------
--  Data-quality gate (shared helper, see 00_partition_utils.sql): the
--  confidence in integer basis points, in [0, 10000].
-- ----------------------------------------------------------------------------
SELECT public.ensure_check_constraint(
    'analysis_signals.portfolio_history_signals',
    'chk_portfolio_history_signals_confidence',
    $chk$confidence BETWEEN 0 AND 10000$chk$);

SELECT public.validate_pending_checks('analysis_signals');

COMMENT ON TABLE analysis_signals.portfolio_history_signals IS 'Portfolio HISTORY SIGNALS (2026-10-21): the PORTFOLIO tier''s event record — one row per coordinated PORTFOLIO EVENT (the composite/pair-native rule cells'' replayed triggers at the pair codes) with THE EVENT DATE (the day the coordination completed) and ``member_signals`` — the association to the member CELL-LEVEL signal events that triggered it (the sourced component cells'' own episode-first records, each WITH ITS OWN DATE — different sub signals on different nearby days; empty array = the pair-native events whose trigger is the pair''s own gauge). The history_signals counterpart at the portfolio grain: the composite events still ride history_signals (the cell tier, the chart markers); this table adds the event-level view + the member association (the component cells'' own history stays dropped from history_signals — the member records here are the one place the triggering cell events are kept). Populated by python -m analysis.analysis_signals (the portfolio families) in the same run as history_signals; --force purges the (sec_type, portfolio_type) tier and re-emits, incremental runs upsert idempotently. Label codes are static.label_dim SMALLINT codes end-to-end — decoded at the API boundary.';
COMMENT ON COLUMN analysis_signals.portfolio_history_signals.code IS 'The PORTFOLIO code — the composite ''A~B'' pair id (mirrors portfolio_signal_rules.code); the event''s identity + search key.';
COMMENT ON COLUMN analysis_signals.portfolio_history_signals.date IS 'THE EVENT DATE — the day the coordinated event completed: every component witness alive within its pairing window, the composite cell''s trigger day (the moment the portfolio event becomes observable). Distinct from the members'' own trigger dates inside member_signals — the components fire on their OWN calendars up to the witness window earlier.';
COMMENT ON COLUMN analysis_signals.portfolio_history_signals.member_signals IS 'THE ASSOCIATION — the member CELL-LEVEL signal events that triggered this portfolio event, as a JSONB array of full cell records [{code, signal_type, signal_sub_type, action, date, signal, signal_reference, confidence}, ...]: the sourced component cells (the sourced mov_rsi rows the portfolio_signal_rules group references) resolved AT THE EVENT level — per component the LATEST episode-first trigger ACROSS ITS TAIL GRID at-or-before the event day (the alive capitulation''s ACTIVE claim: the witness is the raw tail breach, which stays lit while the component sits in its deeper tail — those days carve away from the shallower cell''s episodes, so a per-tail lookup would point at a stale episode). The members can be DIFFERENT SUB SIGNALS on DIFFERENT NEARBY DAYS (the trio entry: market RSI days ago + leg A yesterday + leg B today). Label codes stay SMALLINT inside the payload (static.label_dim domains; API-boundary decode); dates are ''YYYY-MM-DD''; confidence is the member record''s own basis-point value. ''[]'' = the pair-native events (harvest / momentum tilt / wash / rho50 / reclaim / stop) whose trigger is the pair''s own gauge — no sourced component cells participate. The component cells'' own history is deliberately dropped from history_signals (per-code info, the 2026-10-18 source-layer design) — these member records are the ONE place the triggering cell events are kept.';
COMMENT ON COLUMN analysis_signals.portfolio_history_signals.signal IS 'The event day''s composite value that crossed the bar (the sub''s own gauge: rsi_mean for the RSI trio entry, ratio_z for the harvest/tilt, washdepth for the washes, rho_off for the rho50/stop) — the history_signals.signal convention at the composite.';
COMMENT ON COLUMN analysis_signals.portfolio_history_signals.signal_reference IS 'The composite bar crossed — the firing rule cell''s prepared bar, denormalized per event (the trio''s mean lo bar, the harvest''s signed z bar, ...).';
COMMENT ON COLUMN analysis_signals.portfolio_history_signals.confidence IS 'The SCALED confidence on the INTEGER basis-point scale — the composite cell''s depth-scaled record with the starvation/pooled completeness haircuts, identical arithmetic to history_signals.confidence; [0, 10000].';
COMMENT ON COLUMN analysis_signals.portfolio_history_signals.regime_state IS 'The event day''s market regime as its static.label_dim (''regime'') SMALLINT code (0 calm / 1 hot / 2 panic / 3 quiet); 4 = the POOLED stamp — the hedge_pairs family pools on both cell axes today.';
