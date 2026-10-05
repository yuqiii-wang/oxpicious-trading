-- ============================================================================
--  Tables: analysis_signals.signal_thresholds + analysis_signals.history_signals
--
--  The SIGNAL THRESHOLD tier — computed entirely in-engine
--  (analyze/analysis_signals) off analysis.mov_ave_rsi + prices +
--  stats.market_regimes; the forecast layer it once rode on was REMOVED
--  2026-10-05.
--
--    signal_thresholds — one row per QUALIFYING BUCKET (the fixed
--        setup): which config, over which window period
--        (start_date .. end_date = the snapshot stat_date), which side of
--        the indicator, buy/sell, the threshold in the value's own
--        space (live breach target), the confidence-scale parameters
--        the breach depth applies, and whether the live tier flags the
--        breach while it persists (the state families always do; the
--        retired pair-cross event families left with their engine).
--    history_signals — one row per TRIGGER DAY (the event record, a
--        structural clone of live.live_signals): the day's indicator
--        value, the threshold it crossed and the excess. Only dates
--        inside the bucket's own snapshot M are recorded (one
--        snapshot owns each date → no cross-snapshot PK conflicts).
--
--  THE GATE (the plain in-engine rule, identical for every family): on
--  the bucket's blended MIXED row (the fixed 5d 0.65 / next 0.25 /
--  20d 0.10 horizon mix over the triggers' valid anchors),
--    - the sign-aligned blended mean forward change (dir_ave) > 0.75%
--      — a MEAN REVERSAL of material size (top → sell → dir_ave =
--      -ave_change; bottom → buy → dir_ave = +ave_change; the sign
--      alignment is applied at the Python emit layer, NOT in SQL).
--  A strategy IS its delay-0 profile (the 0..5 delay ladder + the
--  optimal-entry argmax were REMOVED 2026-10-05): each qualifying
--  streak's FIRST day is the trigger. Buckets under the per-side
--  sample floor (4 buys / 3 sells) never register.
--
--  THE CONFIDENCE SCALE — confidence_base + the per-row scale model.
--  confidence_base is the bucket's sign-aligned dir_ave: the expected
--  favorable blended move WHEN THE SIGNAL SITS EXACTLY ON THE BAR
--  (zero excess). The recorded breach confidence amplifies that base
--  by the row's scale over the breach depth |excess| =
--  |signal - signal_threshold| (deeper beyond the percentile bar =
--  the more extreme the RSI = the stronger the measured reversal):
--
--      scale(|excess|)  = scale_base + scale_factor * |excess|   -- linear
--      confidence(bps)  = ROUND(10000 * confidence_base * scale)
--
--  scale_method names the curve family ('linear' is the only method
--  the current build stamps; (linear, log, exponential) are reserved).
--  scale_base is the scale at zero excess (default 1.0 — the on-bar
--  row carries exactly its base confidence). Negative scales clamp to
--  0 (a pathological factor must never invert the confidence).
--
--  The emit currently covers mov_rsi (pct = 1, 2, 3, top/bottom);
--  the mov_std / mov_pairs families left with the forecast layer
--  2026-10-05. Other families have no threshold rows until implemented
--  (naturally unticked everywhere).
--
--  Populated by python -m analyze.analysis_signals (incremental at
--  snapshot granularity: the mutable scope — the ROLLING LATEST
--  snapshot and the newest year-end while its forward windows
--  realize — is re-emitted every run; --force deletes the sec_type's
--  rows and re-emits every snapshot on the engine's snapshot grid).
--
--  NO CASE/WHEN anywhere: side is a STORED column (consumers join on
--  it directly instead of deriving action from the side), and every
--  other conditional lives in the Python emit layer.
--
--  The retired signal_strategies table (the pre-scale design: one
--  flat `confidence` column + params JSONB + signal_order rank +
--  signal_delay_days + is_triggered_once) was replaced by
--  signal_thresholds on 2026-10-05 and dropped by
--  temp_scripts/apply_signal_thresholds_migration.py after its rows
--  were migrated (confidence → confidence_base, the scale parameters
--  stamped at their defaults).
-- ============================================================================

-- The retired per-day signals table (threshold rows keyed by signal
-- date) — replaced by the threshold + history design above. CASCADE
-- removes its 16 hash partitions.
DROP TABLE IF EXISTS analysis_signals.signals CASCADE;

DROP TABLE IF EXISTS analysis_signals.history_signals CASCADE;
DROP TABLE IF EXISTS analysis_signals.signal_thresholds CASCADE;

CREATE TABLE analysis_signals.signal_thresholds (
    code            TEXT         NOT NULL,  -- ticker (etf "510050" / index "000300" / stock)
    sec_type        TEXT         NOT NULL,  -- 'etf' | 'index' | 'stock'
    signal_type     TEXT         NOT NULL,  -- 'mov_rsi' — the detection family
    signal_sub_type TEXT         NOT NULL,  -- indicator + window + tail: 'rsi14_1pct' (rsi{W}_{pct}pct); the emission pct rides IN the sub_type so each (window, pct, side) is its own threshold
    ohlcv_type      TEXT,                   -- the retired derived-population cohort verdict — always NULL now (the column stays for the schema)
    side            SMALLINT     NOT NULL,  -- label code (stats.label_dim 'side'): 0 top | 1 bottom — STORED so consumers never derive it
    regime_state    SMALLINT     NOT NULL DEFAULT 0,  -- label code (stats.label_dim 'regime'): the bucket's market-regime split (0 calm / 1 hot / 2 panic / 3 quiet) — STORED like side, PK member so every regime split of one config registers

    start_date      DATE         NOT NULL,  -- the window period start (the snapshot key's trailing-window first day)
    end_date        DATE         NOT NULL,  -- the window period end (== the snapshot stat_date M)

    action          SMALLINT     NOT NULL,  -- label code (stats.label_dim 'action'): 0 buy (bottom — reversal up) | 1 sell (top — reversal down)
    signal_threshold NUMERIC(14,6),         -- the strategy's breach bar in the value's own space: mov_rsi the window's top/bottom-pct% RSI percentile bar
    scale_method     TEXT,                   -- the confidence-scale curve family over the breach excess: 'linear' (the current build's only method; (linear, log, exponential) reserved)
    scale_base       NUMERIC(8,6),           -- the scale factor at zero excess (the signal exactly on the bar; default 1.0)
    scale_factor     NUMERIC(8,6),           -- the per-unit-excess coefficient the scale applies beyond the bar (linear: the slope)
    confidence_base  NUMERIC(8,6),           -- the on-bar confidence the scale starts from (the bucket's sign-aligned blended dir_ave); the breach confidence = ROUND(10000 x confidence_base x scale(|excess|))
    reason          TEXT,                   -- human-readable explanation of the strategy
    is_active       BOOLEAN      NOT NULL DEFAULT FALSE,  -- TRUE on the (code, sec_type, signal_type, signal_sub_type)'s LATEST end_date (refreshed after every run) — the live tier's current threshold set

    CONSTRAINT pk_signal_thresholds PRIMARY KEY (code, sec_type, signal_type, signal_sub_type, side, regime_state, start_date, end_date),
    CONSTRAINT chk_signal_thresholds_action CHECK (action BETWEEN 0 AND 1),
    CONSTRAINT chk_signal_thresholds_regime CHECK (regime_state BETWEEN 0 AND 3),
    CONSTRAINT chk_signal_thresholds_sec_type CHECK (sec_type IN ('stock', 'etf', 'index'))
) PARTITION BY HASH (code);

SELECT public.create_hash_partitions('analysis_signals', 'signal_thresholds', 16);

-- Period-first lookup (UI / active-set fetches by end_date).
CREATE INDEX IF NOT EXISTS idx_signal_thresholds_end_date
    ON analysis_signals.signal_thresholds (sec_type, signal_type, end_date);


-- ----------------------------------------------------------------------------
--  Comments
-- ----------------------------------------------------------------------------
COMMENT ON TABLE analysis_signals.signal_thresholds IS 'Signal THRESHOLDS computed in-engine (the forecast layer was removed 2026-10-05): one row per in-engine bucket whose blended MIXED stats pass the plain gate (sign-aligned blended mean forward change > 0.75% — the sign alignment applied in the Python emit layer). A threshold covers the bucket''s window period (start_date .. end_date = the snapshot stat_date; the trailing 10-year window) and stores the breach bar in the underlying value''s own space plus the confidence-scale parameters (confidence_base + scale_method/scale_base/scale_factor — the breach depth amplifies the base). Populated incrementally by python -m analyze.analysis_signals (mov_rsi pct = 1/2/3; both sides; EVERY regime split of a bucket — regime_state is a PK member, each split registers on its own gate pass).';
COMMENT ON COLUMN analysis_signals.signal_thresholds.sec_type IS 'Security type: etf (ETF), index (CSI-style index), or stock (individual equity).';
COMMENT ON COLUMN analysis_signals.signal_thresholds.code IS 'Ticker. ETFs bare 6-digit (e.g. "510050"); indices bare code (e.g. "000300"); stocks keep their exchange suffix (e.g. "600008.SS").';
COMMENT ON COLUMN analysis_signals.signal_thresholds.signal_type IS 'Detection family: mov_rsi (RSI extreme-percentile strategy) — the only family since the 2026-10-05 retirement of the forecast layer.';
COMMENT ON COLUMN analysis_signals.signal_thresholds.signal_sub_type IS 'Indicator + window + tail width: rsi{W}_{pct}pct for mov_rsi (W = the RSI window, pct = the emitted percentile width 1/2/3 — "1pct"/"2pct"/"3pct"). The pct rides IN the sub_type so each (window, pct, side) is its own threshold and the live rows name their bar width.';
COMMENT ON COLUMN analysis_signals.signal_thresholds.side IS 'The bucket''s own side, STORED (no CASE derivation downstream) as its stats.label_dim (''side'' domain) SMALLINT code — 0 top, 1 bottom (mov_rsi: the RSI top/bottom-pct% percentile tail). Decode at the API boundary only.';
COMMENT ON COLUMN analysis_signals.signal_thresholds.regime_state IS 'The bucket''s market-regime split, STORED like side, as its stats.label_dim (''regime'' domain) SMALLINT code (0 calm / 1 hot / 2 panic / 3 quiet — the stats.market_regimes day label carried by the bucket''s trigger days). Each regime split passes the gate on its OWN blended forward profile and registers its own bar/confidence. PK member (every split of one config coexists); the live tier''s fetch_active_signals returns all, each self-contained (the strongest-breach dedup arbitrates same-bar collisions).';
COMMENT ON COLUMN analysis_signals.signal_thresholds.start_date IS 'The window period start: the snapshot key''s trailing-window first day (stat_date - 10 years + 1 day).';
COMMENT ON COLUMN analysis_signals.signal_thresholds.end_date IS 'The window period end: the snapshot stat_date M the threshold was derived from (the ROLLING LATEST key IS the sec_type''s latest available RSI data date).';
COMMENT ON COLUMN analysis_signals.signal_thresholds.action IS 'Trading action implied by the side, as its stats.label_dim (''action'' domain) SMALLINT code: 1 sell for top extremes (overbought RSI — the measured reversal is downward), 0 buy for bottom extremes (oversold RSI — the measured reversal is upward).';
COMMENT ON COLUMN analysis_signals.signal_thresholds.signal_threshold IS 'The strategy''s breach bar in the underlying value''s own space: for mov_rsi the window''s linear-interpolated top/bottom-pct% RSI quantile (rsi value at the window-end trigger minus its stored trigger excess — constant per bucket). The live tier compares the CURRENT RSI value against this bar directly (sell breaches above, buy below).';
COMMENT ON COLUMN analysis_signals.signal_thresholds.scale_method IS 'The confidence-scale curve family applied over the breach excess: ''linear'' (the only method the current build stamps; (linear, log, exponential) reserved). linear: scale(|excess|) = scale_base + scale_factor * |excess|. Consumers fall back to scale_base (the anchored on-bar point) on an unknown method — never invent a curve.';
COMMENT ON COLUMN analysis_signals.signal_thresholds.scale_base IS 'The scale factor when the signal sits EXACTLY on the threshold (zero excess) — 1.0 by default, so the on-bar row carries exactly its confidence_base.';
COMMENT ON COLUMN analysis_signals.signal_thresholds.scale_factor IS 'The per-unit-excess coefficient the scale applies beyond the bar (linear: the slope). The current build stamps 0.05 — a breach |excess| RSI points deep multiplies the base confidence by 1 + 0.05*|excess|.';
COMMENT ON COLUMN analysis_signals.signal_thresholds.confidence_base IS 'The ON-BAR confidence the scale starts from: the bucket''s sign-aligned blended mean forward change (dir_ave — top negated; the expected favorable blended move of the strategy''s entries; > 0.0075 by the gate). The recorded breach confidence = ROUND(10000 * confidence_base * scale(|signal_excess|)) basis points.';
COMMENT ON COLUMN analysis_signals.signal_thresholds.reason IS 'Human-readable explanation: the window-end trigger''s indicator value vs the bar (e.g. "rsi14_1pct=88.3 >= top-1% bar 86.9 over the 10y window ending 2026-07-31").';
COMMENT ON COLUMN analysis_signals.signal_thresholds.is_active IS 'TRUE only for each (code, sec_type, signal_type, signal_sub_type)''s LATEST end_date row (the freshest snapshot owning that config); FALSE everywhere else. Refreshed after EVERY run (including --force) by run/pipeline.refresh_is_active. Consumers (the live breach monitoring, the UI config menu) use the active rows as the current threshold set.';

-- ----------------------------------------------------------------------------
--  Signal CURVES — the continuous depth-response formulation, emitted by
--  the same engine run as the thresholds (mov_rsi's curve/ subpackage).
--
--  Per (code, rsi_window, side, regime_state, snapshot): the fitted line
--  of the sign-aligned blended forward rebound over the Gaussian depth
--  score s = Phi^-1(1 - q), where q is the trigger day's tail
--  probability (the interpolated plotting-position percentile rank of
--  the day's RSI within the snapshot window):
--
--      mu(s) = mu_center + slope * (s - s_center)
--
--    n          the cell's delay-0 trigger count (streak-merged first
--               qualifying days) over the window — the fit's sample
--    mu_center  y-bar: the cell's sign-aligned blended mean forward
--               change (0.25/0.65/0.10 next/5d/20d, renormalized over
--               valid legs) — AT the cell's own depth center
--    s_center   s-bar: the cell's own mean depth score (computed on
--               demand per cell — NEVER the theoretical 1.755: the
--               streak-merged run-start population, regime selection
--               and per-code trend shift each cell's real center)
--    slope      b: dmu/ds of the OLS fit POOLED per (side, regime)
--               across codes x windows of the snapshot (the depth
--               gradient; buy-side guarded >= 0 at emit, sell side
--               stored as fitted — the 2026-10 RSI depth-response
--               study: exponential-in-percentile == linear-in-probit,
--               no quadratic term anywhere)
--    tail_bars  the (code, window, side)'s linearly-interpolated RSI
--               quantile bars at pct = 1/2/3/5/10 — the denormalized
--               s -> RSI-value mapping ({"1": bar, ...}); each row is
--               self-contained (the family's denormalization rule)
--
--  Curves are the CONFIDENCE MODEL, not a strategy: no 0.75% gate, no
--  is_active (consumers resolve the LATEST end_date per key); every
--  cell with n >= the emit floor registers.
-- ----------------------------------------------------------------------------
CREATE TABLE analysis_signals.signal_curves (
    code            TEXT         NOT NULL,  -- ticker (etf "510050" / index "000300" / stock)
    sec_type        TEXT         NOT NULL,  -- 'etf' | 'index' | 'stock'
    signal_type     TEXT         NOT NULL,  -- 'mov_rsi' — the detection family
    rsi_window      SMALLINT     NOT NULL,  -- the curve's RSI window W (no pct axis — the curve spans the whole q <= 10% tail)
    side            SMALLINT     NOT NULL,  -- label code (stats.label_dim 'side'): 0 top | 1 bottom — STORED so consumers never derive it
    regime_state    SMALLINT     NOT NULL DEFAULT 0,  -- label code (stats.label_dim 'regime'): the cell's market-regime split (0 calm / 1 hot / 2 panic / 3 quiet)

    start_date      DATE         NOT NULL,  -- the window period start (the snapshot key's trailing-window first day)
    end_date        DATE         NOT NULL,  -- the window period end (== the snapshot stat_date M)

    n               INTEGER      NOT NULL,  -- the cell's delay-0 trigger count over the window (the fit's sample)
    mu_center       NUMERIC(10,8) NOT NULL, -- y-bar: the cell's sign-aligned blended mean forward change at its own depth center (buy > 0 = rebound; sell sign-aligned the same way — can be negative)
    s_center        NUMERIC(8,6)  NOT NULL, -- s-bar: the cell's own mean depth score Phi^-1(1-q) of its triggers (empirical, computed on demand — never the theoretical uniform-tail constant)
    slope           NUMERIC(10,8) NOT NULL, -- b: the pooled (side, regime) OLS slope dmu/ds over the snapshot's triggers (buy side guarded >= 0 at emit)
    tail_bars       JSONB        NOT NULL,  -- the (code, window, side)'s interpolated RSI bars at pct 1/2/3/5/10: {"1": bar, "2": ..., "3": ..., "5": ..., "10": ...} — the s -> RSI-value mapping (self-contained row)

    created_at      TIMESTAMP    NOT NULL DEFAULT NOW(),

    CONSTRAINT pk_signal_curves PRIMARY KEY (code, sec_type, signal_type, rsi_window, side, regime_state, start_date, end_date),
    CONSTRAINT chk_signal_curves_side CHECK (side BETWEEN 0 AND 1),
    CONSTRAINT chk_signal_curves_regime CHECK (regime_state BETWEEN 0 AND 3),
    CONSTRAINT chk_signal_curves_sec_type CHECK (sec_type IN ('stock', 'etf', 'index'))
) PARTITION BY HASH (code);

SELECT public.create_hash_partitions('analysis_signals', 'signal_curves', 16);

-- Period-first lookup (UI curve fetches by end_date).
CREATE INDEX IF NOT EXISTS idx_signal_curves_end_date
    ON analysis_signals.signal_curves (sec_type, signal_type, end_date);

COMMENT ON TABLE analysis_signals.signal_curves IS 'Signal CURVES — the continuous RSI depth-response model emitted beside signal_thresholds (python -m analyze.analysis_signals, mov_rsi curve/ subpackage): per (code, rsi_window, side, regime_state, snapshot) the line mu(s) = mu_center + slope * (s - s_center) over the Gaussian depth score s = Phi^-1(1-q) of the trigger day''s tail probability. mu_center is the cell''s own sign-aligned blended mean forward change, s_center the cell''s own empirical mean depth (never a universal constant — streak-merged run starts, regime selection and per-code trend shift each cell''s center), slope the pooled (side, regime) OLS gradient (buy side guarded >= 0). tail_bars carries the interpolated RSI bars at 1/2/3/5/10 pct — the s -> RSI-value mapping, one self-contained row. The 2026-10 depth-response study (10 study indices): exponential-in-percentile == linear-in-probit, regime the first-order effect, sell side adverse at every depth. No gate / no is_active — consumers resolve the LATEST end_date per key.';
COMMENT ON COLUMN analysis_signals.signal_curves.rsi_window IS 'The curve''s RSI window W (3/6/10/14/20/60). No pct axis — the curve spans the whole q <= 10% tail continuously (the threshold table''s discrete {1,2,3}% buckets are points ON this curve).';
COMMENT ON COLUMN analysis_signals.signal_curves.n IS 'The cell''s delay-0 trigger count over the window (each qualifying streak''s first day — the same streak-merge as the threshold tier). The fit''s sample size; cells under the emit floor never register.';
COMMENT ON COLUMN analysis_signals.signal_curves.mu_center IS 'y-bar: the cell''s sign-aligned blended mean forward change (the 0.25/0.65/0.10 next/5d/20d mix, renormalized over valid legs — identical to the threshold tier''s dir_ave unit) evaluated AT the cell''s own depth center. mu(s_center) == mu_center exactly: at the cell''s typical trigger depth the expected rebound is what the threshold tier already measures.';
COMMENT ON COLUMN analysis_signals.signal_curves.s_center IS 's-bar: the cell''s own mean depth score s = Phi^-1(1-q) over its triggers (q = the day''s interpolated percentile rank within the window, the (rank+0.5)/n plotting position). Empirical per cell per snapshot — NEVER hardcoded: the trigger population''s depth mix shifts with regime, streak persistence and per-code trend.';
COMMENT ON COLUMN analysis_signals.signal_curves.slope IS 'b: dmu/ds — the OLS slope of the sign-aligned blended rebound on s, POOLED per (side, regime_state) across the snapshot''s codes x windows (per-cell slopes are too thin; the study found the gradient regime-dependent, window/code roughly invariant). Buy side guarded >= 0 at emit; sell side stored as fitted (the study: sell depth is adverse — the sign carries that information).';
COMMENT ON COLUMN analysis_signals.signal_curves.tail_bars IS 'The (code, rsi_window, side)''s linearly-interpolated RSI quantile bars at tail pct 1/2/3/5/10 as {"1": bar, ...}: bar(pct) is the RSI value whose window-rank equals the pct bar. The denormalized s -> RSI-value mapping so each row is self-contained (the family''s denormalization rule) — consumers plot mu against RSI values or overlay the threshold bars without a second lookup.';


CREATE TABLE analysis_signals.history_signals (
    code            TEXT          NOT NULL,  -- ticker (etf "510050" / index "000300" / stock)
    sec_type        TEXT          NOT NULL,  -- 'etf' | 'index' | 'stock'
    signal_type     TEXT          NOT NULL,  -- 'mov_rsi' — the strategy's detection family
    signal_sub_type TEXT          NOT NULL,  -- 'rsi6_1pct'..'rsi60_3pct' — the strategy this event belongs to
    ohlcv_type      TEXT,                    -- the retired cohort verdict (always NULL) — mirrors signal_thresholds.ohlcv_type
    date            DATE          NOT NULL,  -- the trigger day (inside the strategy's own snapshot M)
    time            TIME          NOT NULL,  -- the bar time of the recorded breach (history rows: 15:00:00 day close)
    signal_delay_days INTEGER     NOT NULL DEFAULT 0,  -- the owning strategy's entry delay (stays in the schema, written 0 — the delay ladder was removed 2026-10-05: the trigger IS the qualifying streak's first day)

    action          SMALLINT      NOT NULL,  -- label code (stats.label_dim 'action'): 1 sell (upward breach, signal_excess > 0) | 0 buy (downward breach, signal_excess < 0)
    signal_excess   NUMERIC(18,6) NOT NULL,  -- signal - signal_threshold: > 0 upward breach (above threshold) | < 0 downward breach (below)
    signal_excess_pct NUMERIC(12,4),         -- (signal_excess / |signal_threshold|) * 100 — unitless breach depth pct; NULL when signal_threshold = 0
    signal          NUMERIC(16,4) NOT NULL,  -- the day's indicator value that crossed the bar (mov_rsi: the RSI value)
    signal_threshold NUMERIC(14,6) NOT NULL, -- the threshold crossed (the strategy's bar — denormalized per event)
    confidence      INTEGER       NOT NULL DEFAULT 100,  -- the SCALED breach confidence in basis points (ROUND(10000 x confidence_base x scale(|signal_excess|)); DEFAULT is the legacy pre-2026-10-05 flat fill)
    is_day_close_trigger BOOLEAN  NOT NULL DEFAULT FALSE,  -- TRUE = history row recorded at the day close (time 15:00:00); FALSE = intraday record
    regime_state    SMALLINT      NOT NULL DEFAULT 0,  -- label code (stats.label_dim 'regime'): the trigger day's market regime (0 calm / 1 hot / 2 panic / 3 quiet)

    created_at      TIMESTAMP     NOT NULL DEFAULT NOW(),  -- record insertion time

    CONSTRAINT pk_history_signals PRIMARY KEY (code, sec_type, signal_type, signal_sub_type, date, time),
    CONSTRAINT chk_history_signals_action CHECK (action BETWEEN 0 AND 1),
    CONSTRAINT chk_history_signals_regime CHECK (regime_state BETWEEN 0 AND 3),
    CONSTRAINT chk_history_signals_sec_type CHECK (sec_type IN ('stock', 'etf', 'index'))
) PARTITION BY HASH (code);

SELECT public.create_hash_partitions('analysis_signals', 'history_signals', 16);

-- Date-first lookup (UI / "signals on day X for sec_type Y").
CREATE INDEX IF NOT EXISTS idx_history_signals_date
    ON analysis_signals.history_signals (sec_type, signal_type, date);

-- ----------------------------------------------------------------------------
--  Comments
-- ----------------------------------------------------------------------------
COMMENT ON TABLE analysis_signals.history_signals IS 'History SIGNAL EVENTS: one row per qualifying trigger day of an analysis_signals.signal_thresholds strategy — the day''s indicator value, the bar it crossed and the excess (a structural clone of live.live_signals so the two tiers read identically). Only dates INSIDE the strategy''s own snapshot M are recorded (one snapshot owns each date → no cross-snapshot PK conflicts). Populated by python -m analyze.analysis_signals together with the threshold rows (same snapshot transaction); mov_rsi pct = 1/2/3 only.';
COMMENT ON COLUMN analysis_signals.history_signals.sec_type IS 'Security type: etf (ETF), index (CSI-style index), or stock (individual equity).';
COMMENT ON COLUMN analysis_signals.history_signals.code IS 'Ticker. ETFs bare 6-digit (e.g. "510050"); indices bare code (e.g. "000300"); stocks keep their exchange suffix (e.g. "600008.SS").';
COMMENT ON COLUMN analysis_signals.history_signals.signal_type IS 'The strategy''s detection family: mov_rsi.';
COMMENT ON COLUMN analysis_signals.history_signals.signal_sub_type IS 'The strategy this event belongs to: rsi{W}_{pct}pct (same format as signal_thresholds.signal_sub_type).';
COMMENT ON COLUMN analysis_signals.history_signals.date IS 'The trigger day — a bucket trigger date inside the strategy''s own snapshot M: each qualifying streak''s FIRST day (the moment the signal becomes observable).';
COMMENT ON COLUMN analysis_signals.history_signals.signal_delay_days IS 'The owning strategy''s entry delay, denormalized per event. Stays in the schema, written 0: the 0..5 delay ladder + the optimal-entry selection were REMOVED 2026-10-05 — the trigger IS the qualifying streak''s first day.';
COMMENT ON COLUMN analysis_signals.history_signals.time IS 'The bar time of the recorded breach: 15:00:00 (the day close) for history rows written by the emit pipeline.';
COMMENT ON COLUMN analysis_signals.history_signals.action IS 'The strategy''s action as its stats.label_dim (''action'' domain) SMALLINT code: 1 sell (top — the day crossed ABOVE its bar, signal_excess > 0) or 0 buy (bottom — crossed BELOW, signal_excess < 0).';
COMMENT ON COLUMN analysis_signals.history_signals.signal IS 'The day''s indicator value that crossed the bar, in the value''s own space (mov_rsi: the RSI value).';
COMMENT ON COLUMN analysis_signals.history_signals.signal_threshold IS 'The bar crossed: value at the trigger day minus its stored trigger excess (mov_rsi: the window''s percentile bar — constant per bucket).';
COMMENT ON COLUMN analysis_signals.history_signals.signal_excess IS 'signal - signal_threshold — the signed breach depth (> 0 above the bar, < 0 below).';
COMMENT ON COLUMN analysis_signals.history_signals.signal_excess_pct IS 'signal_excess / |signal_threshold| * 100 — the unitless breach depth pct; NULL when signal_threshold = 0.';
COMMENT ON COLUMN analysis_signals.history_signals.confidence IS 'The SCALED breach confidence on the INTEGER basis-point scale: ROUND(10000 x signal_thresholds.confidence_base x scale(|signal_excess|)) — the on-bar base (the sign-aligned blended dir_ave) amplified by the owning row''s scale model over the breach depth. (Rows written before the 2026-10-05 scale migration carry the flat ROUND(10000 x dir_ave) of the retired signal_strategies.confidence.)';
COMMENT ON COLUMN analysis_signals.history_signals.is_day_close_trigger IS 'TRUE = day-close history row (time 15:00:00, written by the emit pipeline); FALSE = intraday record (reserved — the live tier writes its own live.live_signals table, not this one).';
COMMENT ON COLUMN analysis_signals.history_signals.regime_state IS 'The trigger day''s market regime as its stats.label_dim (''regime'' domain) SMALLINT code (0 calm / 1 hot / 2 panic / 3 quiet — the stats.market_regimes day label looked up per (code, date)). Recorded, never a gate: the strategy''s bucket is the SAME regime''s split (PK-joined), so the event flags which regime produced it.';

-- ----------------------------------------------------------------------------
--  Data-quality gates (shared helpers, see 00_partition_utils.sql): the
--  confidence scales (thresholds carry the fractional on-bar confidence
--  in [0, 1]; history rows the integer basis points
--  ROUND(10000 x confidence_base x scale), in [0, 10000]) and the
--  excess-sign convention (sell breaches above the bar → excess >= 0;
--  buy below → excess <= 0). The sign requirement carries a 0.0001
--  rounding tolerance: signal stores 4dp while the bar stores 6dp, so a
--  genuine breach can sit within half a 4dp ulp of the bar and the
--  stored excess sign flips inside that slack. NOT VALID first,
--  validated once by the schema sweep below.
-- ----------------------------------------------------------------------------
SELECT public.ensure_check_constraint(
    'analysis_signals.signal_thresholds',
    'chk_signal_thresholds_confidence_base',
    $chk$confidence_base IS NULL OR confidence_base BETWEEN 0 AND 1$chk$);
ALTER TABLE analysis_signals.history_signals
    DROP CONSTRAINT IF EXISTS chk_history_signals_confidence;
SELECT public.ensure_check_constraint(
    'analysis_signals.history_signals',
    'chk_history_signals_confidence',
    $chk$confidence BETWEEN 0 AND 10000$chk$);
SELECT public.ensure_check_constraint(
    'analysis_signals.history_signals',
    'chk_history_signals_excess_sign',
    $chk$(action = 1 AND (signal_excess >= 0
                          OR abs(signal_excess) < 0.0001))
   OR (action = 0 AND (signal_excess <= 0
                          OR abs(signal_excess) < 0.0001))$chk$);

SELECT public.validate_pending_checks('analysis_signals');
