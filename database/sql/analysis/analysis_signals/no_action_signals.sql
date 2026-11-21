-- ============================================================================
--  Table: analysis_signals.no_action_signals
--
--  no_action_signals — the NO-ACTION OVERLAY TIER (2026-11-18,
--  user-directed): one row per (fired positive day x declined
--  action x reason) — the DAY-level negative-confidence marks of
--  engines/no_action (the per-family trigger-time INVALIDATION
--  conditions + the cross-family FATIGUE filter), the sibling of
--  the cell-level REJECT tier (engines/reject.py rides
--  signal_rules/history_signals with negative confidence).
--
--  OVERLAY semantics: a row exists ONLY on a day the positive tier
--  actually fired (it re-states the fired row's own identity,
--  values and the DECLINED action); positive rows are never
--  suppressed, discounted, or rewritten — the strategy/live seams
--  that read history_signals never see this table by construction
--  (no `confidence >= 0` seam edit needed). A fired day may carry
--  SEVERAL reasons (a bandwalk that is also the 4th fired day in
--  the window) — one row each, told apart by reason_code.
--
--  CONSERVATISM by contract (docs/no_action_invalidation_study_
--  2026-11-18.md): rules are calibration-gated (measured separation
--  + rarity <= ~10% of the family's positive history rows), the
--  run-time audit digest re-measures marked-vs-kept every run, and
--  BUY-side rules are structural only (never plunge depth — the
--  standing asymmetry: sharp falls revert, sharp rises continue).
--
--  Populated by python -m analysis.analysis_signals (written by
--  run.run_family after the positive entry loop; force: the
--  (sec_type, signal_type, codes) scope purged then COPY'd,
--  incremental: split upsert past max(date) — the history_signals
--  lifecycle, minus the portfolio tier axis).
-- ============================================================================
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: The no-action overlay events — negative-confidence marks on FIRED positive signal days (the day-level invalidation conditions of engines/no_action: band walks, squeeze breakdowns, whipsaw chop, PE dead zone, macd chop, and the recent-days same-type fatigue filter). One row per fired day x declined action x reason; never suppresses the positive twin.
-- tags: no action, no-action, invalidation, negative confidence, overlay, fatigue, band walk, squeeze, whipsaw, dead zone, chop, 信号失效, 无操作, 不动作, 负置信度, invalidation conditions, signal quality

DROP TABLE IF EXISTS analysis_signals.no_action_signals CASCADE;

CREATE TABLE analysis_signals.no_action_signals (
    code            TEXT          NOT NULL,  -- ticker (index "000300" / etf "510050" / stock) — the fired row's own code
    sec_type        SMALLINT      NOT NULL,  -- label code (static.label_dim 'sec_type'): 0=etf 1=index 2=stock
    signal_type     SMALLINT      NOT NULL,  -- label code (static.label_dim 'signal_type') — the fired row's family
    signal_sub_type SMALLINT      NOT NULL,  -- label code (static.label_dim 'signal_sub_type') — the fired row's sub type
    action          SMALLINT      NOT NULL,  -- label code (static.label_dim 'action'): the DECLINED action — 0 buy / 1 sell (never wait: the mark advises against a real action, it is not an observational outcome)
    reason_code     SMALLINT      NOT NULL,  -- label code (static.label_dim 'no_action_reason'): WHY the fired day is marked (0 fatigue / 1 rsi_bandwalk_sell / 2 boll_squeeze_breakdown / 3 boll_bandwalk_sell / 4 ma_whipsaw / 5 rebound_pe_deadzone / 6 macd_chop)
    date            DATE          NOT NULL,  -- the fired day (the trigger the mark annotates)
    time            TIME          NOT NULL,  -- the fired row's stamp (history rows: 15:00:00 day close)

    signal          NUMERIC(16,6) NOT NULL,  -- the fired row's own indicator value (re-stated — the overlay never invents values)
    signal_reference NUMERIC(14,6) NOT NULL, -- the fired row's own crossed bar
    confidence      INTEGER       NOT NULL,  -- NEGATIVE bp only (see the CHECK below) — a small DELIBERATE magnitude (config NO_ACTION_CONF_BP): the marker is the product, the magnitude a severity hint
    regime_state    SMALLINT,  -- the fired day's recorded regime context (nullable — carried from the fired row for display; never a gate)
    rule_params     JSONB         NOT NULL DEFAULT '{}'::jsonb,  -- the CONSTANT condition (the reason vocabulary, the bars, the evidence tag — the same constant-rule convention as signal_rules)

    created_at      TIMESTAMP     NOT NULL DEFAULT NOW(),

    CONSTRAINT pk_no_action_signals PRIMARY KEY (code, sec_type, signal_type, signal_sub_type, action, reason_code, date, time),
    CONSTRAINT chk_no_action_action CHECK (action BETWEEN 0 AND 1),
    CONSTRAINT chk_no_action_sec_type CHECK (sec_type BETWEEN 0 AND 2),
    CONSTRAINT chk_no_action_regime CHECK (regime_state IS NULL OR regime_state BETWEEN 0 AND 4)
) PARTITION BY HASH (code);

SELECT public.create_hash_partitions('analysis_signals', 'no_action_signals', 16);

-- Date-first lookup (UI / reason distribution queries).
CREATE INDEX IF NOT EXISTS idx_no_action_signals_date
    ON analysis_signals.no_action_signals (sec_type, signal_type, date);

-- ----------------------------------------------------------------------------
--  Data-quality gates (shared helpers, see 00_partition_utils.sql):
--  the NEGATIVE half-plane only — this tier NEVER carries positive
--  confidence (advice against an action, never for it; magnitudes
--  capped by config, well inside the basis-point range).
-- ----------------------------------------------------------------------------
SELECT public.ensure_check_constraint(
    'analysis_signals.no_action_signals',
    'chk_no_action_confidence',
    $chk$confidence BETWEEN -10000 AND 0$chk$);

SELECT public.validate_pending_checks('analysis_signals');

COMMENT ON TABLE analysis_signals.no_action_signals IS 'NO-ACTION OVERLAY EVENTS: one row per (fired positive day x declined action x reason) — the day-level negative-confidence marks of engines/no_action (per-family invalidation conditions + the fatigue filter), overlaying (never suppressing) the positive signals. Populated by python -m analysis.analysis_signals alongside history_signals.';
COMMENT ON COLUMN analysis_signals.no_action_signals.action IS 'The DECLINED action as its static.label_dim (''action'') domain SMALLINT code: 0 buy / 1 sell — the mark reads "do NOT take THIS action at this price" (never the inverse action; a NO SELL is not a buy). Never 2 wait: the mark annotates a fired real action.';
COMMENT ON COLUMN analysis_signals.no_action_signals.reason_code IS 'The static.label_dim (''no_action_reason'') domain SMALLINT code — WHY the fired day is marked: 0 fatigue (>= N distinct fired days of the (code, type, action) in the trailing window — chop), 1 rsi_bandwalk_sell (top-tail trigger mid-rise with an agreeing guard — tops continue), 2 boll_squeeze_breakdown (a -k breach out of a squeezed band — compression continues), 3 boll_bandwalk_sell (+k breach riding an expanding band), 4 ma_whipsaw (reclaim inside a below->above flip chop), 5 rebound_pe_deadzone (capitulation at middle PE — the barbell dead zone), 6 macd_chop (noise-scale cross inside rapid alternation).';
COMMENT ON COLUMN analysis_signals.no_action_signals.confidence IS 'NEGATIVE basis points only (CHECK (-10000, 0]): the mark''s severity hint from config NO_ACTION_CONF_BP — a small DELIBERATE magnitude (the marker is the product). Mirrors the REJECT tier''s negative-confidence convention (history_signals since 2026-11-16) at the DAY level.';
COMMENT ON COLUMN analysis_signals.no_action_signals.rule_params IS 'The CONSTANT condition stamped on every row (the signal_rules convention): the reason vocabulary, the declined actions/sub types, the condition''s bar constants and evidence tag — the UI renders them verbatim.';
