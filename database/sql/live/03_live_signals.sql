-- ============================================================================
--  Table: live.live_signals
--
--  Breach RECORDS for the analysis_signals threshold set: one row per
--  (code, sec_type, signal_type, signal_sub_type, date, time) tick/bar at
--  which the monitored value BREACHED the config's signal_threshold. The
--  table is a pure append-only RECORD — no state, no aggregation; each
--  row documents ONE breach observation (when, which config, which
--  direction, at what value, against which threshold).
--
--  Threshold source: analysis_signals.signal_strategies (the is_active =
--  TRUE rows — each config's LATEST end_date snapshot — form the current
--  per-config threshold set). The breached threshold is denormalized into
--  signal_threshold so the record stays self-contained — strategy
--  snapshots are month-immutable, but the live monitor may run against
--  any later refresh, so the record pins the value it actually compared
--  against.
--
--  Semantics per row:
--    signal_excess = signal - signal_threshold; its SIGN is the breach
--    direction: positive = upward breach (signal > signal_threshold —
--    the sell-side extremes: mov_rsi top / mov_std upper); negative =
--    downward breach (signal < signal_threshold — the buy-side
--    extremes: mov_rsi bottom / mov_std lower).
--  Correlation with action (enforced by convention, not constraint:
--  sell ↔ positive excess, buy ↔ negative excess) is denormalized into
--  action for direct filtering by trading direction.
--
--  Day-close trigger rows: is_day_close_trigger = TRUE marks a
--  15:00:00 observation evaluated against the OFFICIAL DAILY CLOSE —
--  written by the on-demand --date mode (python -m live.live_signals
--  --date D) when the selected date has NO intraday bar for the code
--  (the no-intraday-data fallback; value sources bounded to rows
--  at-or-before D). TRUE rows also remain from the retired --live
--  day-close mirror writer (the historical record lives in
--  analysis_signals.history_signals now). Intraday rows — the live
--  monitor AND the --date mode's last-intraday-bar replay — keep FALSE.
--
--  NO FK to analysis_signals.signal_strategies: the strategy PK carries
--  the forecast period (start_date .. end_date), not the live breach
--  date, and active rows are refreshed in place — the record only
--  documents the breach.
-- ============================================================================

CREATE TABLE IF NOT EXISTS live.live_signals (
    code            TEXT          NOT NULL,  -- ticker (etf "510050" / index "000300" / stock)
    sec_type        TEXT          NOT NULL,  -- 'etf' | 'index' | 'stock'
    signal_type     TEXT          NOT NULL,  -- 'mov_rsi' | 'mov_std' — breached config's detection family
    signal_sub_type TEXT          NOT NULL,  -- 'rsi6'..'rsi60' / 'std5_2'..'std60_2' — breached config
    date            DATE          NOT NULL,  -- live date of the breach
    time            TIME          NOT NULL,  -- tick/bar time of the breach (intraday)

    action          SMALLINT      NOT NULL,  -- label code (static.label_dim 'action'): 1 sell (upward breach, signal_excess > 0) | 0 buy (downward breach, signal_excess < 0)
    signal_excess   NUMERIC(18,6) NOT NULL,  -- signal - signal_threshold: > 0 upward breach (above threshold) | < 0 downward breach (below)
    signal_excess_pct NUMERIC(12,4),         -- (signal_excess / |signal_threshold|) * 100 — unitless breach depth pct; NULL when signal_threshold = 0 (guarded by NULLIF)
    signal          NUMERIC(16,4) NOT NULL,  -- the breaching value at this tick (for mov_rsi rows: the RSI value that breached its threshold)
    signal_threshold NUMERIC(14,6) NOT NULL, -- the threshold breached (denormalized from analysis_signals.signal_strategies)
    confidence      INTEGER       NOT NULL DEFAULT 100,  -- the strategy's expected-move confidence in basis points (ROUND(10000 × strategies.confidence); DEFAULT is the legacy pre-2026-09-25 fill)
    confidence_boost INTEGER,     -- the same bucket's @boost cohort confidence (bp) — add-on context, never a gate; NULL = no child / non-mov_rsi
    confidence_selection INTEGER, -- the same bucket's @selection cohort confidence (bp) — add-on context, never a gate; NULL = no child / bottom-only / non-mov_rsi
    is_day_close_trigger BOOLEAN  NOT NULL DEFAULT FALSE,  -- TRUE = legacy day-close mirror row (time 15:00:00); FALSE = intraday live-monitor breach
    regime_state    SMALLINT      NOT NULL DEFAULT 0,  -- label code (static.label_dim 'regime'): the breach bar's DATE regime (0 calm / 1 hot / 2 panic / 3 quiet)

    created_at      TIMESTAMP     NOT NULL DEFAULT NOW(),  -- record audit

    CONSTRAINT pk_live_signals PRIMARY KEY (code, sec_type, signal_type, signal_sub_type, date, time),
    CONSTRAINT chk_live_signals_action CHECK (action BETWEEN 0 AND 1),
    CONSTRAINT chk_live_signals_regime CHECK (regime_state BETWEEN 0 AND 3),
    CONSTRAINT chk_live_signals_sec_type CHECK (sec_type IN ('stock', 'etf', 'index'))
) PARTITION BY HASH (code);

SELECT public.create_hash_partitions('live', 'live_signals', 8);

-- Day-scoped lookup: all breaches of a date (the UI / monitor's dominant pattern).
CREATE INDEX IF NOT EXISTS idx_live_signals_date
    ON live.live_signals (sec_type, date, time);

-- Existing installs gain the regime column here — BEFORE the label-code
-- migration below maps it (fresh installs get the SMALLINT column from
-- the CREATE body).
ALTER TABLE live.live_signals
    ADD COLUMN IF NOT EXISTS regime_state TEXT NOT NULL DEFAULT 'calm';

-- ----------------------------------------------------------------------------
--  Comments
-- ----------------------------------------------------------------------------
COMMENT ON TABLE live.live_signals IS 'Append-only breach RECORDS for the analysis_signals threshold set: one row per (code, sec_type, signal_type, signal_sub_type, date, time) intraday tick at which the monitored value crossed the config''s signal_threshold — price vs band level for mov_std configs, current RSI vs its top/bottom-1% threshold for mov_rsi configs (indicator space; the signal column holds the compared value). Pure record of breach observations — thresholds are denormalized so each row is self-contained; the current threshold set is the is_active rows of analysis_signals.signal_strategies. Populated by python -m live.live_signals (intraday, is_day_close_trigger = FALSE).';
COMMENT ON COLUMN live.live_signals.sec_type IS 'Security type: etf (ETF), index (CSI-style index), or stock (individual equity).';
COMMENT ON COLUMN live.live_signals.code IS 'Ticker. ETFs bare 6-digit (e.g. "510050"); indices bare code (e.g. "000300"); stocks keep their exchange suffix (e.g. "600008.SS").';
COMMENT ON COLUMN live.live_signals.signal_type IS 'Breached config''s detection family: mov_rsi (RSI extreme-percentile threshold) or mov_std (Bollinger band level) — mirrors analysis_signals.signal_strategies.signal_type.';
COMMENT ON COLUMN live.live_signals.signal_sub_type IS 'Breached config''s indicator + window: rsi{W} for mov_rsi, std{W}_{k} for mov_std — mirrors analysis_signals.signal_strategies.signal_sub_type.';
COMMENT ON COLUMN live.live_signals.date IS 'The live date on which the breach was observed.';
COMMENT ON COLUMN live.live_signals.time IS 'The intraday tick/bar time of the breach observation; 15:00:00 on legacy day-close mirror rows (is_day_close_trigger = TRUE).';
COMMENT ON COLUMN live.live_signals.action IS 'Trading direction of the breached config as its static.label_dim (''action'' domain) SMALLINT code: 1 sell (top RSI / upper band — upward breach, positive excess) or 0 buy (bottom RSI / lower band — downward breach, negative excess). Denormalized from analysis_signals.signal_thresholds.action for direct filtering.';
COMMENT ON COLUMN live.live_signals.signal_excess IS 'Signed excess of the breaching value over the threshold: signal - signal_threshold (exactly, on the stored columns). Positive = upward breach (signal above signal_threshold), negative = downward breach (below); the sign is correlated with action by convention (sell ↔ positive, buy ↔ negative). Day-close boundary rows (selected within the threshold-rounding tolerance) may carry a tiny opposite-signed excess since signal is stored at 4 decimals.';
COMMENT ON COLUMN live.live_signals.signal IS 'The value that breached the threshold: the live price at this tick for mov_std rows, the current RSI (analysis.mov_ave_rsi latest row) for mov_rsi rows — RSI thresholds live on the 0-100 scale, so an RSI-vs-threshold breach is recorded in indicator space.';
COMMENT ON COLUMN live.live_signals.signal_threshold IS 'The threshold the value crossed, denormalized from analysis_signals.signal_strategies.signal_threshold of the config''s active row — the record pins the value it was compared against.';

COMMENT ON COLUMN live.live_signals.confidence IS 'The breached strategy''s expected-move confidence (integer, basis points) = ROUND(10000 × analysis_signals.signal_strategies.confidence) — the chosen rung''s sign-aligned blended mean forward change (dir_ave, a fractional expected move) scaled to bp, copied from the active strategy row at breach time; column DEFAULT 100 only fills legacy rows. (Was the 0-100 percent scale of the removed reverse_prob until 2026-09-25 — pre-rewrite rows keep their old-scale values.)';
COMMENT ON COLUMN live.live_signals.is_day_close_trigger IS 'TRUE = day-close observation (time 15:00:00): written by the on-demand --date mode (python -m live.live_signals --date D) when the selected date has no intraday bar for the code — the official daily close from stats.{sec_type}_basic_stats, value sources bounded to rows at-or-before D (plus remaining rows of the retired --live day-close mirror writer; the historical record lives in analysis_signals.history_signals); FALSE (default) = intraday-bar breach (live monitor, or the --date mode''s last-intraday-bar replay). The trigger kind of the observation.';
COMMENT ON COLUMN live.live_signals.regime_state IS 'The breach bar''s DATE regime as its static.label_dim (''regime'' domain) SMALLINT code (0 calm / 1 hot / 2 panic / 3 quiet — the stats.market_regimes day label), resolved per check by (code, date), so as-of --date replays see exactly the D verdict (daily states are static historical labels, no peeking; shift-1 trailing inputs make every label known at its own day''s close). RECORD, never a gate: the breach still fires; the label marks which regime produced the event and matches the strategy''s own regime split. Replaces the retired is_market_hyped boolean.';
COMMENT ON COLUMN live.live_signals.created_at IS 'Row insertion timestamp (record audit).';
COMMENT ON COLUMN live.live_signals.signal_excess_pct IS 'Unitless breach depth: (signal_excess / |signal_threshold|) * 100. Signed like signal_excess (positive = upward/sell, negative = downward/buy). Guarded against divide-by-zero with NULLIF — only meaningful when signal_threshold ≠ 0. Stored so the identity signal_excess_pct = signal_excess / |signal_threshold| * 100 holds exactly (computed from the same rounded signal_excess).';
COMMENT ON COLUMN live.live_signals.confidence_boost IS 'The SAME bucket''s ohlcv_boost cohort confidence (integer basis points) = ROUND(10000 × signal_strategies.confidence) of the @boost child row sharing the breached strategy''s (code, sec_type, signal_type, side, regime_state, start_date, end_date) — the ohlcv-score-trust-weighted measurement of the same trigger days (analysis.analysis_forecasts.ohlcv_boost), its own chosen-rung dir_ave, never blended into the parent confidence. ADD-ON context only: the child rows are permanently is_active = FALSE (never breached, never threshold-set) and this column never gates anything. NULL = no @boost child registered for the bucket, or a family without ohlcv children (mov_std / mov_pairs / mov_pairs_ema).';
COMMENT ON COLUMN live.live_signals.confidence_selection IS 'The SAME bucket''s ohlcv_selection cohort confidence (integer basis points) = ROUND(10000 × signal_strategies.confidence) of the @selection child row sharing the breached strategy''s identity — the favorable-anatomy subset''s own chosen-rung dir_ave (analysis.analysis_forecasts.ohlcv_selection; the streaks whose day-0 ohlcv score cleared the side''s bar — bottom side only, the top side carries no selection child). ADD-ON context only, same never-a-gate rule as confidence_boost. NULL = no @selection child registered (incl. every top-side breach) or a family without ohlcv children.';

-- ----------------------------------------------------------------------------
--  label-code migration (2026-10-05): existing TEXT action/regime_state
--  columns convert IN PLACE to their static.label_dim SMALLINT codes —
--  live breach rows are append-only records that cannot be regenerated,
--  so no truncate. Fresh installs already have the SMALLINT columns
--  from the CREATE body (this block no-ops). MUST run BEFORE the
--  data-quality gates below (the excess-sign gate compares the column
--  to integers). The retired 2026-09 is_market_hyped fold + TEXT
--  realignment blocks it replaces already ran everywhere their input
--  existed. Dropping the TEXT columns auto-drops their IN-list CHECKs;
--  the gates below re-create the BETWEEN forms.
-- ----------------------------------------------------------------------------
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_schema = 'live'
                 AND table_name = 'live_signals'
                 AND column_name = 'action'
                 AND data_type = 'text') THEN
        ALTER TABLE live.live_signals
            ADD COLUMN action_code SMALLINT NOT NULL DEFAULT 0,
            ADD COLUMN regime_state_code SMALLINT NOT NULL DEFAULT 0;
        UPDATE live.live_signals
           SET action_code = CASE action WHEN 'sell' THEN 1 ELSE 0 END;
        UPDATE live.live_signals
           SET regime_state_code = CASE regime_state
                                       WHEN 'hot'   THEN 1
                                       WHEN 'panic' THEN 2
                                       WHEN 'quiet' THEN 3
                                       ELSE 0 END;
        ALTER TABLE live.live_signals
            DROP COLUMN action,
            DROP COLUMN regime_state;
        ALTER TABLE live.live_signals
            RENAME COLUMN action_code TO action;
        ALTER TABLE live.live_signals
            RENAME COLUMN regime_state_code TO regime_state;
    END IF;
END $$;

-- ----------------------------------------------------------------------------
--  Data-quality gates (shared helpers, see 00_partition_utils.sql): the
--  0-10000 basis-point confidence scale (mirroring
--  analysis_signals.history_signals, the structural twin this table
--  clones) and the excess-sign convention (sell breaches above
--  the bar → excess >= 0; buy below → excess <= 0). The sign requirement
--  carries a 0.0001 rounding tolerance: signal stores 4dp while the bar
--  stores 6dp, so a genuine breach can sit within half a 4dp ulp of the
--  bar and the stored excess sign flips inside that slack (observed max
--  0.00005 — the engine only records rows triggered on the RAW value).
--  NOT VALID first, validated once by the schema sweep below.
-- ----------------------------------------------------------------------------
-- 2026-09-25 confidence re-sourcing: drop the retired 0-100 form first
-- (ensure_check_constraint skips a same-named constraint regardless of
-- its definition; legacy 0-100 rows stay valid inside the widened
-- range).
ALTER TABLE live.live_signals
    DROP CONSTRAINT IF EXISTS chk_live_signals_confidence;
SELECT public.ensure_check_constraint(
    'live.live_signals',
    'chk_live_signals_confidence',
    $chk$confidence BETWEEN 0 AND 10000$chk$);
-- 2026-09-28 cohort add-on columns: the 0-10000 bp gates on the
-- denormalized cohort confidences. NULL passes (CHECK semantics) —
-- non-mov_rsi rows legitimately carry no cohort values.
SELECT public.ensure_check_constraint(
    'live.live_signals',
    'chk_live_signals_conf_boost',
    $chk$confidence_boost IS NULL
       OR confidence_boost BETWEEN 0 AND 10000$chk$);
SELECT public.ensure_check_constraint(
    'live.live_signals',
    'chk_live_signals_conf_selection',
    $chk$confidence_selection IS NULL
       OR confidence_selection BETWEEN 0 AND 10000$chk$);
-- label-code vocab gates (fresh installs carry these from the CREATE
-- body; migrated installs lost them with the dropped TEXT columns).
SELECT public.ensure_check_constraint(
    'live.live_signals',
    'chk_live_signals_action',
    $chk$action BETWEEN 0 AND 1$chk$);
SELECT public.ensure_check_constraint(
    'live.live_signals',
    'chk_live_signals_regime',
    $chk$regime_state BETWEEN 0 AND 3$chk$);
SELECT public.ensure_check_constraint(
    'live.live_signals',
    'chk_live_signals_excess_sign',
    $chk$(action = 1 AND (signal_excess >= 0
                          OR abs(signal_excess) < 0.0001))
   OR (action = 0 AND (signal_excess <= 0
                          OR abs(signal_excess) < 0.0001))$chk$);

SELECT public.validate_pending_checks('live');
