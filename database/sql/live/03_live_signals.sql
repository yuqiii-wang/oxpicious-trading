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
    code            TEXT          NOT NULL,  -- ticker (etf "510050.SS" / index "000300" / stock)
    sec_type        TEXT          NOT NULL,  -- 'etf' | 'index' | 'stock'
    signal_type     TEXT          NOT NULL,  -- 'mov_rsi' | 'mov_std' — breached config's detection family
    signal_sub_type TEXT          NOT NULL,  -- 'rsi6'..'rsi60' / 'std5_2'..'std60_2' — breached config
    date            DATE          NOT NULL,  -- live date of the breach
    time            TIME          NOT NULL,  -- tick/bar time of the breach (intraday)

    action          TEXT          NOT NULL,  -- 'sell' (upward breach, signal_excess > 0) | 'buy' (downward breach, signal_excess < 0)
    signal_excess   NUMERIC(18,6) NOT NULL,  -- signal - signal_threshold: > 0 upward breach (above threshold) | < 0 downward breach (below)
    signal_excess_pct NUMERIC(12,4),         -- (signal_excess / |signal_threshold|) * 100 — unitless breach depth pct; NULL when signal_threshold = 0 (guarded by NULLIF)
    signal          NUMERIC(16,4) NOT NULL,  -- the breaching value at this tick (for mov_rsi rows: the RSI value that breached its threshold)
    signal_threshold NUMERIC(14,6) NOT NULL, -- the threshold breached (denormalized from analysis_signals.signal_strategies)
    confidence      INTEGER       NOT NULL DEFAULT 100,  -- the strategy's forecast confidence on the 0-100 scale
    is_day_close_trigger BOOLEAN  NOT NULL DEFAULT FALSE,  -- TRUE = legacy day-close mirror row (time 15:00:00); FALSE = intraday live-monitor breach
    regime_state    TEXT          NOT NULL DEFAULT 'calm',  -- the breach bar's DATE regime (stats.market_regimes day label: calm/hot/panic/quiet)

    created_at      TIMESTAMP     NOT NULL DEFAULT NOW(),  -- record insertion time

    CONSTRAINT pk_live_signals PRIMARY KEY (code, sec_type, signal_type, signal_sub_type, date, time),
    CONSTRAINT chk_live_signals_action CHECK (action IN ('buy', 'sell')),
    CONSTRAINT chk_live_signals_regime CHECK (regime_state IN ('calm', 'hot', 'panic', 'quiet')),
    CONSTRAINT chk_live_signals_sec_type CHECK (sec_type IN ('stock', 'etf', 'index'))
) PARTITION BY HASH (code);

SELECT public.create_hash_partitions('live', 'live_signals', 8);

-- Day-scoped lookup: all breaches of a date (the UI / monitor's dominant pattern).
CREATE INDEX IF NOT EXISTS idx_live_signals_date
    ON live.live_signals (sec_type, date, time);

-- Existing installs gain the regime column here — BEFORE the COMMENT
-- below references it (fresh installs get it from the CREATE body).
ALTER TABLE live.live_signals
    ADD COLUMN IF NOT EXISTS regime_state TEXT NOT NULL DEFAULT 'calm';

-- ----------------------------------------------------------------------------
--  Comments
-- ----------------------------------------------------------------------------
COMMENT ON TABLE live.live_signals IS 'Append-only breach RECORDS for the analysis_signals threshold set: one row per (code, sec_type, signal_type, signal_sub_type, date, time) intraday tick at which the monitored value crossed the config''s signal_threshold — price vs band level for mov_std configs, current RSI vs its top/bottom-1% threshold for mov_rsi configs (indicator space; the signal column holds the compared value). Pure record of breach observations — thresholds are denormalized so each row is self-contained; the current threshold set is the is_active rows of analysis_signals.signal_strategies. Populated by python -m live.live_signals (intraday, is_day_close_trigger = FALSE).';
COMMENT ON COLUMN live.live_signals.sec_type IS 'Security type: etf (ETF), index (CSI-style index), or stock (individual equity).';
COMMENT ON COLUMN live.live_signals.code IS 'Ticker. ETFs use exchange suffix (e.g. "510050.SS"); indices use bare code (e.g. "000300").';
COMMENT ON COLUMN live.live_signals.signal_type IS 'Breached config''s detection family: mov_rsi (RSI extreme-percentile threshold) or mov_std (Bollinger band level) — mirrors analysis_signals.signal_strategies.signal_type.';
COMMENT ON COLUMN live.live_signals.signal_sub_type IS 'Breached config''s indicator + window: rsi{W} for mov_rsi, std{W}_{k} for mov_std — mirrors analysis_signals.signal_strategies.signal_sub_type.';
COMMENT ON COLUMN live.live_signals.date IS 'The live date on which the breach was observed.';
COMMENT ON COLUMN live.live_signals.time IS 'The intraday tick/bar time of the breach observation; 15:00:00 on legacy day-close mirror rows (is_day_close_trigger = TRUE).';
COMMENT ON COLUMN live.live_signals.action IS 'Trading direction of the breached config: sell (top RSI / upper band — upward breach, positive excess) or buy (bottom RSI / lower band — downward breach, negative excess). Denormalized from analysis_signals.signal_strategies.action for direct filtering.';
COMMENT ON COLUMN live.live_signals.signal_excess IS 'Signed excess of the breaching value over the threshold: signal - signal_threshold (exactly, on the stored columns). Positive = upward breach (signal above signal_threshold), negative = downward breach (below); the sign is correlated with action by convention (sell ↔ positive, buy ↔ negative). Day-close boundary rows (selected within the threshold-rounding tolerance) may carry a tiny opposite-signed excess since signal is stored at 4 decimals.';
COMMENT ON COLUMN live.live_signals.signal IS 'The value that breached the threshold: the live price at this tick for mov_std rows, the current RSI (analysis.mov_ave_rsi latest row) for mov_rsi rows — RSI thresholds live on the 0-100 scale, so an RSI-vs-threshold breach is recorded in indicator space.';
COMMENT ON COLUMN live.live_signals.signal_threshold IS 'The threshold the value crossed, denormalized from analysis_signals.signal_strategies.signal_threshold of the config''s active row — the record pins the value it was compared against.';

COMMENT ON COLUMN live.live_signals.confidence IS 'The breached strategy''s forecast confidence weight (integer, 0-100 scale) = ROUND(100 × analysis_signals.signal_strategies.confidence) — the source bucket''s mixed-row reverse_prob (a [0,1] probability) scaled to percent, copied from the active strategy row at breach time; column DEFAULT 100 only fills rows written before the confidence column existed on the source.';
COMMENT ON COLUMN live.live_signals.is_day_close_trigger IS 'TRUE = day-close observation (time 15:00:00): written by the on-demand --date mode (python -m live.live_signals --date D) when the selected date has no intraday bar for the code — the official daily close from stats.{sec_type}_basic_stats, value sources bounded to rows at-or-before D (plus remaining rows of the retired --live day-close mirror writer; the historical record lives in analysis_signals.history_signals); FALSE (default) = intraday-bar breach (live monitor, or the --date mode''s last-intraday-bar replay). The trigger kind of the observation.';
COMMENT ON COLUMN live.live_signals.regime_state IS 'The breach bar''s DATE regime — the stats.market_regimes day label (calm / hot / panic / quiet), resolved per check by (code, date), so as-of --date replays see exactly the D verdict (daily states are static historical labels, no peeking; shift-1 trailing inputs make every label known at its own day''s close). RECORD, never a gate: the breach still fires; the label marks which regime produced the event and matches the strategy''s own regime split. Replaces the retired is_market_hyped boolean.';
COMMENT ON COLUMN live.live_signals.created_at IS 'Row insertion timestamp (record audit).';
COMMENT ON COLUMN live.live_signals.signal_excess_pct IS 'Unitless breach depth: (signal_excess / |signal_threshold|) * 100. Signed like signal_excess (positive = upward/sell, negative = downward/buy). Guarded against divide-by-zero with NULLIF — only meaningful when signal_threshold ≠ 0. Stored so the identity signal_excess_pct = signal_excess / |signal_threshold| * 100 holds exactly (computed from the same rounded signal_excess).';

-- ----------------------------------------------------------------------------
--  Data-quality gates (shared helpers, see 00_partition_utils.sql): the
--  0-100 confidence scale (mirroring analysis_signals.history_signals, the
--  structural twin this table clones) and the excess-sign convention (sell breaches above
--  the bar → excess >= 0; buy below → excess <= 0). The sign requirement
--  carries a 0.0001 rounding tolerance: signal stores 4dp while the bar
--  stores 6dp, so a genuine breach can sit within half a 4dp ulp of the
--  bar and the stored excess sign flips inside that slack (observed max
--  0.00005 — the engine only records rows triggered on the RAW value).
--  NOT VALID first, validated once by the schema sweep below.
-- ----------------------------------------------------------------------------
SELECT public.ensure_check_constraint(
    'live.live_signals',
    'chk_live_signals_confidence',
    $chk$confidence BETWEEN 0 AND 100$chk$);
SELECT public.ensure_check_constraint(
    'live.live_signals',
    'chk_live_signals_excess_sign',
    $chk$(action = 'sell' AND (signal_excess >= 0
                              OR abs(signal_excess) < 0.0001))
   OR (action = 'buy'  AND (signal_excess <= 0
                              OR abs(signal_excess) < 0.0001))$chk$);

SELECT public.validate_pending_checks('live');

-- ----------------------------------------------------------------------------
--  market-regime migration (2026-09): regime_state ships in the CREATE
--  body above for fresh installs; existing installs gain it here
--  (metadata-only ADD COLUMN, default 'calm'). The retired boolean is
--  folded in (TRUE -> 'hot', FALSE -> 'calm' — the closest successor
--  semantics) and dropped; rows are then realigned with the daily
--  states table where it exists. Idempotent.
-- ----------------------------------------------------------------------------
ALTER TABLE live.live_signals
    ADD COLUMN IF NOT EXISTS regime_state TEXT NOT NULL DEFAULT 'calm';

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_schema = 'live'
                 AND table_name = 'live_signals'
                 AND column_name = 'is_market_hyped') THEN
        UPDATE live.live_signals
        SET regime_state = CASE WHEN is_market_hyped
                                THEN 'hot' ELSE 'calm' END;
        ALTER TABLE live.live_signals DROP COLUMN is_market_hyped;
    END IF;
    IF to_regclass('stats.market_regimes') IS NOT NULL THEN
        UPDATE live.live_signals l
        SET regime_state = r.regime
        FROM stats.market_regimes r
        WHERE r.sec_type = l.sec_type
          AND r.code = l.code
          AND r.date = l.date
          AND l.regime_state <> r.regime;
    END IF;
END $$;

-- ----------------------------------------------------------------------------
--  regime vocab gate on existing installs (fresh installs get it from
--  the CREATE body above). NOT VALID first, validated by the sweep.
-- ----------------------------------------------------------------------------
SELECT public.ensure_check_constraint(
    'live.live_signals',
    'chk_live_signals_regime',
    $chk$regime_state IN ('calm', 'hot', 'panic', 'quiet')$chk$);
SELECT public.validate_pending_checks('live');
