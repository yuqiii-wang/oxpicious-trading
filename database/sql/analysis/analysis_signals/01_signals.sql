-- ============================================================================
--  Tables: analysis_signals.signal_strategies + analysis_signals.history_signals
--
--  The SIGNAL STRATEGY tier over analysis_forecasts. A forecast bucket
--  (code × stat_month snapshot M × config, trailing 5-year window
--  (M - 5y, M]) whose MIXED forecast_results row passes the plain gate
--  IS a signal strategy for that forecast period; the bucket's trigger
--  days inside the snapshot month M are its history signals.
--
--    signal_strategies — one row per QUALIFYING BUCKET (the fixed
--        setup): which config, over which forecast period
--        (start_date .. end_date = the snapshot month), which side of
--        the indicator, buy/sell, the threshold in the value's own
--        space (live breach target) and the gate confidence.
--    history_signals — one row per TRIGGER DAY (the event record, a
--        structural clone of live.live_signals): the day's indicator
--        value, the threshold it crossed and the excess. Only dates
--        inside the bucket's own snapshot month M are recorded (one
--        snapshot owns each date → no cross-month PK conflicts).
--
--  THE GATE (the plain forecast-results rule, identical for every
--  family): on the bucket's MIXED forecast_results row,
--    - the sign-aligned blended mean forward change (dir_ave) > 1% —
--      a MEAN REVERSAL of material size (top/upper → sell → dir_ave =
--      -ave_change; bottom/lower → buy → dir_ave = +ave_change; the
--      sign alignment is applied at the Python emit layer, NOT in
--      SQL), AND
--    - the blended reverse_prob > 1% (a material reversal probability).
--  confidence = that mixed row's reverse_prob. The emit currently
--  covers mov_rsi (pct = 1, top/bottom) and mov_std (MA/σ windows
--  >= 60d at k >= 2.0σ, upper/lower); other forecast families have no
--  strategy rows until implemented (naturally unticked everywhere).
--
--  Populated by python -m analyze.analysis_signals (incremental at
--  stat-month granularity; the newest REFRESH_MONTHS months are
--  re-emitted every run; --force deletes the sec_type's rows and
--  re-emits every month present in analysis_forecasts).
--
--  NO CASE/WHEN anywhere: side is a STORED column (consumers join on
--  it directly instead of deriving action from the side), and every
--  other conditional lives in the Python emit layer.
-- ============================================================================

-- The retired per-day signals table (threshold rows keyed by signal
-- date) — replaced by the strategy + history design above. CASCADE
-- removes its 16 hash partitions.
DROP TABLE IF EXISTS analysis_signals.signals CASCADE;

CREATE TABLE IF NOT EXISTS analysis_signals.signal_strategies (
    code            TEXT         NOT NULL,  -- ticker (etf "510050.SS" / index "000300" / stock)
    sec_type        TEXT         NOT NULL,  -- 'etf' | 'index' | 'stock'
    signal_type     TEXT         NOT NULL,  -- 'mov_rsi' | 'mov_std' — the detection family
    signal_sub_type TEXT         NOT NULL,  -- indicator + window: 'rsi14' / 'std20_2' (k %g-formatted: 2.0 → "2", 2.5 → "2.5")
    side            TEXT         NOT NULL,  -- the bucket's side: 'top' | 'bottom' (mov_rsi) / 'upper' | 'lower' (mov_std) — STORED so consumers never derive it
    is_market_hyped BOOLEAN      NOT NULL DEFAULT FALSE,  -- the bucket's hype split (mirrors the forecast bucket's own is_market_hyped — STORED like side, PK member so BOTH splits of one config register)

    start_date      DATE         NOT NULL,  -- the forecast period start (stat_month - 5y + 1 day — the bucket's trailing-window first day)
    end_date        DATE         NOT NULL,  -- the forecast period end (== the snapshot stat_month M of analysis_forecasts)

    action          TEXT         NOT NULL,  -- 'sell' (top/upper — the reversal direction is down) | 'buy' (bottom/lower — the reversal direction is up)
    signal_threshold NUMERIC(14,6),         -- the strategy's breach bar in the value's own space: mov_rsi the window's top/bottom-1% RSI percentile bar; mov_std the band level ma_W ± k·std_Wdays at the window-end trigger (price space)
    confidence      NUMERIC(8,6),           -- the bucket's MIXED forecast_results reverse_prob (the gate's own probability)
    reason          TEXT,                   -- human-readable explanation of the strategy
    params          JSONB,                  -- full strategy params as JSON: the bucket config + the gate row's blended forward profile (dir_ave / reverse_prob / occurrence_count)
    signal_order    INTEGER,                -- 1-based best-first rank within (sec_type, end_date) by confidence DESC (1 = the month's most-confident strategy; nothing is trimmed)
    is_active       BOOLEAN      NOT NULL DEFAULT FALSE,  -- TRUE on the (code, sec_type, signal_type, signal_sub_type)'s LATEST end_date (refreshed after every run) — the live tier's current threshold set

    CONSTRAINT pk_signal_strategies PRIMARY KEY (code, sec_type, signal_type, signal_sub_type, side, is_market_hyped, start_date, end_date),
    CONSTRAINT chk_signal_strategies_action CHECK (action IN ('buy', 'sell')),
    CONSTRAINT chk_signal_strategies_sec_type CHECK (sec_type IN ('stock', 'etf', 'index'))
) PARTITION BY HASH (code);

SELECT public.create_hash_partitions('analysis_signals', 'signal_strategies', 16);

-- Period-first lookup (UI / active-set fetches by end_date).
CREATE INDEX IF NOT EXISTS idx_signal_strategies_end_date
    ON analysis_signals.signal_strategies (sec_type, signal_type, end_date);

-- Existing installs gain the hype-split column here (fresh installs get
-- it from the CREATE body above) — before the column COMMENT and the PK
-- migration block below, both of which reference it.
ALTER TABLE analysis_signals.signal_strategies
    ADD COLUMN IF NOT EXISTS is_market_hyped BOOLEAN NOT NULL DEFAULT FALSE;

-- ----------------------------------------------------------------------------
--  Comments
-- ----------------------------------------------------------------------------
COMMENT ON TABLE analysis_signals.signal_strategies IS 'Signal STRATEGIES derived from analysis_forecasts: one row per forecast bucket whose MIXED forecast_results row passes the plain gate (sign-aligned blended mean forward change > 1% AND blended reverse_prob > 1% — the sign alignment applied in the Python emit layer). A strategy covers the bucket''s forecast period (start_date .. end_date = the snapshot stat_month; the trailing 5-year window (M - 5y, M]) and stores the breach bar in the underlying value''s own space for the live tier. Populated incrementally by python -m analyze.analysis_signals (mov_rsi pct = 1; mov_std MA/σ windows >= 60d at k >= 2.0σ; both sides; BOTH hype splits of a bucket — is_market_hyped is a PK member, each split registers on its own gate pass).';
COMMENT ON COLUMN analysis_signals.signal_strategies.sec_type IS 'Security type: etf (ETF), index (CSI-style index), or stock (individual equity).';
COMMENT ON COLUMN analysis_signals.signal_strategies.code IS 'Ticker. ETFs use exchange suffix (e.g. "510050.SS"); indices use bare code (e.g. "000300").';
COMMENT ON COLUMN analysis_signals.signal_strategies.signal_type IS 'Detection family: mov_rsi (RSI extreme-percentile strategy) or mov_std (Bollinger band breach strategy) — mirrors the matching analysis_forecasts bucket table.';
COMMENT ON COLUMN analysis_signals.signal_strategies.signal_sub_type IS 'Indicator + window: rsi{W} for mov_rsi (W = RSI window 6/10/14/20/60), std{W}_{k} for mov_std (W = MA/σ window 5/20/60, k = the σ multiple %g-formatted — 2.0 renders "2", 2.5 renders "2.5" — band ma_{W} ± k·std_{W}days).';
COMMENT ON COLUMN analysis_signals.signal_strategies.side IS 'The bucket''s own side, STORED (no CASE derivation downstream): top/bottom for mov_rsi (RSI top/bottom-1% percentile), upper/lower for mov_std (above/below the band).';
COMMENT ON COLUMN analysis_signals.signal_strategies.is_market_hyped IS 'The bucket''s hype split, STORED like side: TRUE rows are calibrated on the bucket''s hyped-day trigger set (days inside the code''s stats.mov_ave_market_hypes episodes), FALSE rows on the normal-day set — each split passes the gates on its OWN MIXED forward profile and registers its own bar/confidence. PK member since 2026-09-19 (both splits of one config coexist); the live tier''s fetch_active_signals returns both, each self-contained (the strongest-breach dedup arbitrates same-bar collisions), and the forecast-table tick joins on the row''s OWN hype state.';
COMMENT ON COLUMN analysis_signals.signal_strategies.start_date IS 'The forecast period start: stat_month - 5 years + 1 day — the first day of the bucket''s trailing window.';
COMMENT ON COLUMN analysis_signals.signal_strategies.end_date IS 'The forecast period end: the snapshot stat_month M the strategy was derived from (the analysis_forecasts month; also the tick-join key for the UI forecast tables).';
COMMENT ON COLUMN analysis_signals.signal_strategies.action IS 'Trading action implied by the side: sell for top/upper extremes (overbought RSI / above-band — the measured reversal is downward), buy for bottom/lower extremes (oversold RSI / below-band — the measured reversal is upward).';
COMMENT ON COLUMN analysis_signals.signal_strategies.signal_threshold IS 'The strategy''s breach bar in the underlying value''s own space: for mov_rsi the window''s linear-interpolated top/bottom-1% RSI quantile (rsi value at the window-end trigger minus its stored trigger excess — constant per bucket); for mov_std the band level ma_{W} ± k·std_{W}days at the window-end trigger day (price space). The live tier compares the CURRENT value against this bar directly (sell breaches above, buy below).';
COMMENT ON COLUMN analysis_signals.signal_strategies.confidence IS 'The matching forecast bucket''s MIXED forecast_results reverse_prob: P(the blended forward window''s adverse path extreme crosses the fixed ±1% bar) — the gate rule''s own probability. NULL when the bucket has no results.';
COMMENT ON COLUMN analysis_signals.signal_strategies.reason IS 'Human-readable explanation: the window-end trigger''s indicator value vs the bar (e.g. "rsi14=88.3 >= top-1% bar 86.9 over the 5y window ending 2026-07-31").';
COMMENT ON COLUMN analysis_signals.signal_strategies.params IS 'Full strategy parameters as JSON: the bucket config keys (family-specific) + the gate row''s blended forward profile (conf_period = ''mixed'', dir_ave / reverse_prob / occurrence_count).';
COMMENT ON COLUMN analysis_signals.signal_strategies.signal_order IS 'Best-first rank WITHIN its own (sec_type, end_date) pool: 1 = the month''s most-confident strategy. Ordered by confidence DESC (the gate''s forecast confidence = the bucket''s mixed-row reverse_prob), PK tuple as the deterministic tiebreak. Nothing is trimmed — every gate-passing strategy is kept. Re-stamped by python -m analyze.analysis_signals after every run.';
COMMENT ON COLUMN analysis_signals.signal_strategies.is_active IS 'TRUE only for each (code, sec_type, signal_type, signal_sub_type)''s LATEST end_date row (the freshest forecast snapshot owning that config); FALSE everywhere else. Refreshed by python -m analyze.analysis_signals after EVERY run (including --force). Consumers (the live breach monitoring, the UI config menu) use the active rows as the current threshold set.';

CREATE TABLE IF NOT EXISTS analysis_signals.history_signals (
    code            TEXT          NOT NULL,  -- ticker (etf "510050.SS" / index "000300" / stock)
    sec_type        TEXT          NOT NULL,  -- 'etf' | 'index' | 'stock'
    signal_type     TEXT          NOT NULL,  -- 'mov_rsi' | 'mov_std' — the strategy's detection family
    signal_sub_type TEXT          NOT NULL,  -- 'rsi6'..'rsi60' / 'std5_2'..'std60_2' — the strategy this event belongs to
    date            DATE          NOT NULL,  -- the trigger day (inside the strategy's own snapshot month M)
    time            TIME          NOT NULL,  -- the bar time of the recorded breach (history rows: 15:00:00 day close)

    action          TEXT          NOT NULL,  -- 'sell' (upward breach, signal_excess > 0) | 'buy' (downward breach, signal_excess < 0)
    signal_excess   NUMERIC(18,6) NOT NULL,  -- signal - signal_threshold: > 0 upward breach (above threshold) | < 0 downward breach (below)
    signal_excess_pct NUMERIC(12,4),         -- (signal_excess / |signal_threshold|) * 100 — unitless breach depth pct; NULL when signal_threshold = 0
    signal          NUMERIC(16,4) NOT NULL,  -- the day's indicator value that crossed the bar (mov_rsi: the RSI value; mov_std: the close)
    signal_threshold NUMERIC(14,6) NOT NULL, -- the threshold crossed (the strategy's bar — denormalized per event)
    confidence      INTEGER       NOT NULL DEFAULT 100,  -- the strategy's forecast confidence on the 0-100 scale (ROUND(100 × mixed reverse_prob))
    is_day_close_trigger BOOLEAN  NOT NULL DEFAULT FALSE,  -- TRUE = history row recorded at the day close (time 15:00:00); FALSE = intraday record
    is_market_hyped BOOLEAN       NOT NULL DEFAULT FALSE,  -- the trigger day sits inside one of the code's stats.mov_ave_market_hypes episodes (any check-in window — the forecast side's union convention)

    created_at      TIMESTAMP     NOT NULL DEFAULT NOW(),  -- record insertion time

    CONSTRAINT pk_history_signals PRIMARY KEY (code, sec_type, signal_type, signal_sub_type, date, time),
    CONSTRAINT chk_history_signals_action CHECK (action IN ('buy', 'sell')),
    CONSTRAINT chk_history_signals_sec_type CHECK (sec_type IN ('stock', 'etf', 'index'))
) PARTITION BY HASH (code);

SELECT public.create_hash_partitions('analysis_signals', 'history_signals', 16);

-- Date-first lookup (UI / "signals on day X for sec_type Y").
CREATE INDEX IF NOT EXISTS idx_history_signals_date
    ON analysis_signals.history_signals (sec_type, signal_type, date);

-- ----------------------------------------------------------------------------
--  Comments
-- ----------------------------------------------------------------------------
COMMENT ON TABLE analysis_signals.history_signals IS 'History SIGNAL EVENTS: one row per qualifying trigger day of an analysis_signals.signal_strategies strategy — the day''s indicator value, the bar it crossed and the excess (a structural clone of live.live_signals so the two tiers read identically). Only dates INSIDE the strategy''s own snapshot month M are recorded (one snapshot owns each date → no cross-month PK conflicts). Populated by python -m analyze.analysis_signals together with the strategy rows (same month transaction); mov_rsi pct = 1 and mov_std (>= 60d windows, k >= 2.0) only.';
COMMENT ON COLUMN analysis_signals.history_signals.sec_type IS 'Security type: etf (ETF), index (CSI-style index), or stock (individual equity).';
COMMENT ON COLUMN analysis_signals.history_signals.code IS 'Ticker. ETFs use exchange suffix (e.g. "510050.SS"); indices use bare code (e.g. "000300").';
COMMENT ON COLUMN analysis_signals.history_signals.signal_type IS 'The strategy''s detection family: mov_rsi or mov_std.';
COMMENT ON COLUMN analysis_signals.history_signals.signal_sub_type IS 'The strategy this event belongs to: rsi{W} / std{W}_{k} (same %g k-formatting as signal_strategies.signal_sub_type).';
COMMENT ON COLUMN analysis_signals.history_signals.date IS 'The trigger day — a bucket trigger date inside the strategy''s own snapshot month M.';
COMMENT ON COLUMN analysis_signals.history_signals.time IS 'The bar time of the recorded breach: 15:00:00 (the day close) for history rows written by the emit pipeline.';
COMMENT ON COLUMN analysis_signals.history_signals.action IS 'The strategy''s action: sell (top/upper — the day crossed ABOVE its bar, signal_excess > 0) or buy (bottom/lower — crossed BELOW, signal_excess < 0).';
COMMENT ON COLUMN analysis_signals.history_signals.signal IS 'The day''s indicator value that crossed the bar, in the value''s own space (mov_rsi: the RSI value; mov_std: the close).';
COMMENT ON COLUMN analysis_signals.history_signals.signal_threshold IS 'The bar crossed: value at the trigger day minus its stored trigger excess (mov_rsi: the window''s percentile bar; mov_std: the day''s band level ma_{W} ± k·std_{W}days).';
COMMENT ON COLUMN analysis_signals.history_signals.signal_excess IS 'signal - signal_threshold — the signed breach depth (> 0 above the bar, < 0 below).';
COMMENT ON COLUMN analysis_signals.history_signals.signal_excess_pct IS 'signal_excess / |signal_threshold| * 100 — the unitless breach depth pct; NULL when signal_threshold = 0.';
COMMENT ON COLUMN analysis_signals.history_signals.confidence IS 'The owning strategy''s forecast confidence on the 0-100 INTEGER scale: ROUND(100 × the bucket''s mixed-row reverse_prob).';
COMMENT ON COLUMN analysis_signals.history_signals.is_day_close_trigger IS 'TRUE = day-close history row (time 15:00:00, written by the emit pipeline); FALSE = intraday record (reserved — the live tier writes its own live.live_signals table, not this one).';
COMMENT ON COLUMN analysis_signals.history_signals.is_market_hyped IS 'TRUE when the trigger day falls inside one of the code''s stats.mov_ave_market_hypes episodes (ANY min_checkin_period — the same union convention the forecast bucket splits use). Recorded, never a gate: strategies are calibrated on non-hyped buckets only, so a hyped-day event flags the regime the reversal stats were NOT calibrated on. Structurally FALSE at emit time (the strategy''s bucket is the non-hyped split); kept truthful against the CURRENT episode table — the wholesale episode rebuild can revise history, and the idempotent backfill below re-aligns pre-existing rows.';

-- ----------------------------------------------------------------------------
--  Data-quality gates (shared helpers, see 00_partition_utils.sql): the
--  confidence scales (strategies carry the 0-1 reverse_prob; history rows
--  the 0-100 integer) and the excess-sign convention (sell breaches above
--  the bar → excess >= 0; buy below → excess <= 0). The sign requirement
--  carries a 0.0001 rounding tolerance: signal stores 4dp while the bar
--  stores 6dp, so a genuine breach can sit within half a 4dp ulp of the
--  bar and the stored excess sign flips inside that slack (observed max
--  0.00005 — the engine only records rows triggered on the RAW value).
--  NOT VALID first, validated once by the schema sweep below.
-- ----------------------------------------------------------------------------
SELECT public.ensure_check_constraint(
    'analysis_signals.signal_strategies',
    'chk_signal_strategies_confidence',
    $chk$confidence BETWEEN 0 AND 1$chk$);
SELECT public.ensure_check_constraint(
    'analysis_signals.history_signals',
    'chk_history_signals_confidence',
    $chk$confidence BETWEEN 0 AND 100$chk$);
SELECT public.ensure_check_constraint(
    'analysis_signals.history_signals',
    'chk_history_signals_excess_sign',
    $chk$(action = 'sell' AND (signal_excess >= 0
                              OR abs(signal_excess) < 0.0001))
   OR (action = 'buy'  AND (signal_excess <= 0
                              OR abs(signal_excess) < 0.0001))$chk$);

SELECT public.validate_pending_checks('analysis_signals');

-- ----------------------------------------------------------------------------
--  is_market_hyped migration (2026-09-19): the column ships in the CREATE
--  body above for fresh installs; existing installs gain it here
--  (metadata-only ADD COLUMN on the partitioned parent — instant). The
--  backfill then re-aligns pre-existing rows with the CURRENT episode
--  table (idempotent — rows already TRUE, or with no covering episode,
--  are untouched).
-- ----------------------------------------------------------------------------
ALTER TABLE analysis_signals.history_signals
    ADD COLUMN IF NOT EXISTS is_market_hyped BOOLEAN NOT NULL DEFAULT FALSE;

UPDATE analysis_signals.history_signals h
SET is_market_hyped = TRUE
WHERE NOT h.is_market_hyped
  AND EXISTS (SELECT 1 FROM stats.mov_ave_market_hypes e
              WHERE e.sec_type = h.sec_type
                AND e.code = h.code
                AND e.start_date <= h.date
                AND e.end_date >= h.date);

-- ----------------------------------------------------------------------------
--  is_market_hyped PK migration (2026-09-19): hyped buckets now register
--  strategies too. The column ships in the CREATE body above for fresh
--  installs; existing installs gain it here, and the PK is rebuilt to
--  include it (all pre-existing rows are the FALSE split — one default
--  value, so the rebuild is conflict-free; DROP + re-ADD on the
--  partitioned parent cascades the constrained indexes to the children).
--  Idempotent: the DO block checks the PK's column set first.
-- ----------------------------------------------------------------------------
DO $$
DECLARE
    pk_cols text;
BEGIN
    SELECT string_agg(a.attname, ',' ORDER BY x.ord)
    INTO pk_cols
    FROM pg_constraint c
    JOIN pg_class t ON t.oid = c.conrelid
    JOIN pg_namespace n ON n.oid = t.relnamespace
    JOIN LATERAL unnest(c.conkey) WITH ORDINALITY AS x(attnum, ord) ON TRUE
    JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = x.attnum
    WHERE n.nspname = 'analysis_signals'
      AND t.relname = 'signal_strategies'
      AND c.contype = 'p';
    IF pk_cols IS DISTINCT FROM
       'code,sec_type,signal_type,signal_sub_type,side,is_market_hyped,start_date,end_date'
    THEN
        ALTER TABLE analysis_signals.signal_strategies
            ADD COLUMN IF NOT EXISTS is_market_hyped BOOLEAN NOT NULL DEFAULT FALSE;
        ALTER TABLE analysis_signals.signal_strategies
            DROP CONSTRAINT pk_signal_strategies;
        ALTER TABLE analysis_signals.signal_strategies
            ADD CONSTRAINT pk_signal_strategies PRIMARY KEY
            (code, sec_type, signal_type, signal_sub_type, side,
             is_market_hyped, start_date, end_date);
        RAISE NOTICE 'signal_strategies PK rebuilt with is_market_hyped';
    END IF;
END $$;
