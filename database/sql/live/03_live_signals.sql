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
--  Threshold source: analysis_signals.signals (the is_active = TRUE rows
--  form the current per-config threshold set). The breached threshold is
--  denormalized into signal_threshold so the record stays self-contained —
--  analysis_signals snapshots are month-immutable, but the live monitor
--  may run against any later refresh, so the record pins the value it
--  actually compared against.
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
--  Day-close trigger rows: is_day_close_trigger = TRUE marks rows written
--  by `python -m analyze.analysis_signals --live`, which mirrors every
--  not-yet-recorded analysis_signals day as ONE observation at that day's
--  close (time 15:00:00; close vs band level for mov_std, day RSI vs its
--  threshold for mov_rsi). Intraday rows written by the live monitor keep
--  the default FALSE.
--
--  NO FK to analysis_signals.signals: the signals PK carries the
--  detection snapshot month (date), not the live breach date, and active
--  rows are refreshed in place — the record only documents the breach.
-- ============================================================================

CREATE TABLE IF NOT EXISTS live.live_signals (
    code            TEXT          NOT NULL,  -- ticker (etf "510050.SS" / index "000300" / stock)
    sec_type        TEXT          NOT NULL,  -- 'etf' | 'index' | 'stock'
    signal_type     TEXT          NOT NULL,  -- 'mov_rsi' | 'mov_std' — breached config's detection family
    signal_sub_type TEXT          NOT NULL,  -- 'rsi6'..'rsi60' / 'std5'..'std60' — breached config
    date            DATE          NOT NULL,  -- live date of the breach
    time            TIME          NOT NULL,  -- tick/bar time of the breach (intraday)

    action          TEXT          NOT NULL,  -- 'sell' (upward breach, signal_excess > 0) | 'buy' (downward breach, signal_excess < 0)
    signal_excess   NUMERIC(18,6) NOT NULL,  -- signal - signal_threshold: > 0 upward breach (above threshold) | < 0 downward breach (below)
    signal_excess_pct NUMERIC(12,4),         -- (signal_excess / |signal_threshold|) * 100 — unitless breach depth pct; NULL when signal_threshold = 0 (guarded by NULLIF)
    signal          NUMERIC(16,4) NOT NULL,  -- the breaching value at this tick (for mov_rsi rows: the RSI value that breached its threshold)
    signal_threshold NUMERIC(14,6) NOT NULL, -- the threshold breached (denormalized from analysis_signals.signals)
    confidence      INTEGER       NOT NULL DEFAULT 100,  -- signal confidence weight (fixed 100 for now)
    is_day_close_trigger BOOLEAN  NOT NULL DEFAULT FALSE,  -- TRUE = day-close mirror row (time 15:00:00, analyze.analysis_signals --live); FALSE = intraday live-monitor breach

    created_at      TIMESTAMP     NOT NULL DEFAULT NOW(),  -- record insertion time

    CONSTRAINT pk_live_signals PRIMARY KEY (code, sec_type, signal_type, signal_sub_type, date, time),
    CONSTRAINT chk_live_signals_action CHECK (action IN ('buy', 'sell')),
    CONSTRAINT chk_live_signals_sec_type CHECK (sec_type IN ('stock', 'etf', 'index'))
) PARTITION BY HASH (code);

SELECT public.create_hash_partitions('live', 'live_signals', 8);

-- Day-scoped lookup: all breaches of a date (the UI / monitor's dominant pattern).
CREATE INDEX IF NOT EXISTS idx_live_signals_date
    ON live.live_signals (sec_type, date, time);

-- ----------------------------------------------------------------------------
--  Comments
-- ----------------------------------------------------------------------------
COMMENT ON TABLE live.live_signals IS 'Append-only breach RECORDS for the analysis_signals threshold set: one row per (code, sec_type, signal_type, signal_sub_type, date, time) intraday tick at which the monitored value crossed the config''s signal_threshold — price vs band level for mov_std configs, current RSI vs its top/bottom-1% threshold for mov_rsi configs (indicator space; the signal column holds the compared value). Pure record of breach observations — thresholds are denormalized so each row is self-contained; the current threshold set is the is_active rows of analysis_signals.signals. Populated by python -m live.live_signals (intraday, is_day_close_trigger = FALSE) and python -m analyze.analysis_signals --live (day-close mirror at 15:00:00, is_day_close_trigger = TRUE).';
COMMENT ON COLUMN live.live_signals.sec_type IS 'Security type: etf (ETF), index (CSI-style index), or stock (individual equity).';
COMMENT ON COLUMN live.live_signals.code IS 'Ticker. ETFs use exchange suffix (e.g. "510050.SS"); indices use bare code (e.g. "000300").';
COMMENT ON COLUMN live.live_signals.signal_type IS 'Breached config''s detection family: mov_rsi (RSI extreme-percentile threshold) or mov_std (Bollinger band level) — mirrors analysis_signals.signals.signal_type.';
COMMENT ON COLUMN live.live_signals.signal_sub_type IS 'Breached config''s indicator + window: rsi{W} for mov_rsi, std{W} for mov_std — mirrors analysis_signals.signals.signal_sub_type.';
COMMENT ON COLUMN live.live_signals.date IS 'The live date on which the breach was observed.';
COMMENT ON COLUMN live.live_signals.time IS 'The intraday tick/bar time of the breach observation; 15:00:00 for day-close trigger rows (is_day_close_trigger = TRUE).';
COMMENT ON COLUMN live.live_signals.action IS 'Trading direction of the breached config: sell (top RSI / upper band — upward breach, positive excess) or buy (bottom RSI / lower band — downward breach, negative excess). Denormalized from analysis_signals.signals.action for direct filtering.';
COMMENT ON COLUMN live.live_signals.signal_excess IS 'Signed excess of the breaching value over the threshold: signal - signal_threshold (exactly, on the stored columns). Positive = upward breach (signal above signal_threshold), negative = downward breach (below); the sign is correlated with action by convention (sell ↔ positive, buy ↔ negative). Day-close boundary rows (selected within the threshold-rounding tolerance) may carry a tiny opposite-signed excess since signal is stored at 4 decimals.';
COMMENT ON COLUMN live.live_signals.signal IS 'The value that breached the threshold: the live price at this tick for mov_std rows, the current RSI (analysis.mov_ave_rsi latest row) for mov_rsi rows — RSI thresholds live on the 0-100 scale, so an RSI-vs-threshold breach is recorded in indicator space.';
COMMENT ON COLUMN live.live_signals.signal_threshold IS 'The threshold the value crossed, denormalized from analysis_signals.signals.signal_threshold of the config''s active row — the record pins the value it was compared against.';

COMMENT ON COLUMN live.live_signals.confidence IS 'Final signal confidence weight (integer, 0-100 scale) = ROUND(100 × analysis_signals.signals.confidence) — the source config''s forecast confidence (MAX reverse_prob across 5d+20d forecast periods, a [0,1] probability) scaled to percent. Written by BOTH live writers (intraday evaluator + day-close mirror); column DEFAULT 100 only fills rows written before the confidence column existed on the source.';
COMMENT ON COLUMN live.live_signals.is_day_close_trigger IS 'TRUE = day-close mirror row written by python -m analyze.analysis_signals --live (one observation per not-yet-recorded analysis_signals day at that day''s close, time 15:00:00); FALSE (default) = intraday live-monitor breach. The trigger kind of the observation.';
COMMENT ON COLUMN live.live_signals.created_at IS 'Row insertion timestamp (record audit).';
COMMENT ON COLUMN live.live_signals.signal_excess_pct IS 'Unitless breach depth: (signal_excess / |signal_threshold|) * 100. Signed like signal_excess (positive = upward/sell, negative = downward/buy). Guarded against divide-by-zero with NULLIF — only meaningful when signal_threshold ≠ 0. Stored so the identity signal_excess_pct = signal_excess / |signal_threshold| * 100 holds exactly (computed from the same rounded signal_excess).';

-- ----------------------------------------------------------------------------
--  Migration #5 — add signal_excess_pct column (idempotent)
-- ----------------------------------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'live'
          AND table_name   = 'live_signals'
          AND column_name  = 'signal_excess_pct'
    ) THEN
        ALTER TABLE live.live_signals
            ADD COLUMN signal_excess_pct NUMERIC(12,4);

        -- Backfill from stored signal_excess / signal_threshold.
        -- NULLIF guards against any historical row with threshold = 0.
        UPDATE live.live_signals
        SET    signal_excess_pct =
                   signal_excess / NULLIF(ABS(signal_threshold), 0) * 100
        WHERE  signal_excess_pct IS NULL;
    END IF;
END $$;

-- ----------------------------------------------------------------------------
--  Migration #6 — sync confidence to the ×100 scale (idempotent)
--
--  live.live_signals.confidence is now ROUND(100 × the source config's
--  analysis confidence) on a 0-100 INTEGER scale — both writers scale
--  SQL-side (exact NUMERIC rounding). This migration syncs every row
--  that differs from its source (joined via the signal key
--  code, sec_type, signal_type, signal_sub_type, date), covering:
--    • rows still at the legacy fixed default (confidence = 100),
--    • rows that stored the RAW [0,1] reverse_prob probability,
--    • writer rows predating the exact-rounding convention.
--  Intraday rows on a day whose signal was not emitted at close have
--  no source row and keep their stored value. Re-runs are no-ops.
-- ----------------------------------------------------------------------------
UPDATE live.live_signals l
SET    confidence = ROUND(s.confidence * 100)
FROM   analysis_signals.signals s
WHERE  l.code = s.code
  AND  l.sec_type = s.sec_type
  AND  l.signal_type = s.signal_type
  AND  l.signal_sub_type = s.signal_sub_type
  AND  l.date = s.date
  AND  s.confidence IS NOT NULL
  AND  l.confidence <> ROUND(s.confidence * 100);
