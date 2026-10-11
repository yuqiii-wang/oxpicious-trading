-- ============================================================================
--  Tables: analysis_signals.signal_thresholds + analysis_signals.history_signals
--
--  The SIGNAL THRESHOLD tier — computed entirely in-engine
--  (analysis/analysis_signals) off analysis.mov_ave_rsi + prices +
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
--    history_signals — one row per TRIGGER DAY x POPULATION VARIANT
--        (the event record, a structural clone of live.live_signals):
--        FALSE = the plain event, TRUE = the ohlcv-boost twin (the same
--        day, w-scaled confidence). Only dates inside the bucket's own
--        snapshot M are recorded (one snapshot owns each date → no
--        cross-snapshot PK conflicts).
--
--  THE GATE (the plain in-engine rule, identical for every family): on
--  the bucket's blended MIXED row (the fixed 5d 0.65 / next 0.25 /
--  20d 0.10 horizon mix over the triggers' valid anchors),
--    - the sign-aligned blended mean forward change (dir_ave) > its
--      side's gate bar (buy 0.75% / sell 0% — GATE_DIR_AVE_MIN /
--      GATE_DIR_AVE_MIN_SELL)
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
--  The emit covers mov_rsi (pct = 1, 2, 3, top/bottom) and boll_std
--  (2026-10-07: the FIXED-σ Bollinger band breaches — NO percentile
--  logic, the excess σ-depth over the bands replaces the pct axis; k =
--  2 / 2.5 / 3σ, sub_type boll{W}_{k}std, the bar the FIXED ±k
--  constant in the z space, the breach excess depth |z| − k in σ
--  units); the mov_pairs families left with the forecast layer
--  2026-10-05. Other families have no threshold rows until implemented
--  (naturally unticked everywhere).
--
--  Populated by python -m analysis.analysis_signals (incremental at
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
    sec_type        SMALLINT     NOT NULL,  -- label code (stats.label_dim 'sec_type'): 0=etf 1=index 2=stock
    signal_type     SMALLINT     NOT NULL,  -- label code (stats.label_dim 'signal_type'): 0=mov_rsi 1=bollinger — the detection family
    signal_sub_type TEXT         NOT NULL,  -- indicator + window + tail: 'rsi14_1pct' (rsi{W}_{pct}pct); the emission pct rides IN the sub_type so each (window, pct, side) is its own threshold
    is_ohlcv_boosted BOOLEAN    NOT NULL DEFAULT FALSE,  -- the bucket's day-0 ohlcv-boost verdict (engines/ohlcv_boost: the trigger-day OHLC/vol x amt/RSI-depth score clears the side's bar) — an analysis marker, NEVER a gate
    side            SMALLINT     NOT NULL,  -- label code (stats.label_dim 'side'): 0 top | 1 bottom — STORED so consumers never derive it
    regime_state    SMALLINT     NOT NULL DEFAULT 0,  -- label code (stats.label_dim 'regime'): the bucket's market-regime split (0 calm / 1 hot / 2 panic / 3 quiet) — STORED like side, PK member so every regime split of one config registers

    start_date      DATE         NOT NULL,  -- the window period start (the snapshot key's trailing-window first day)
    end_date        DATE         NOT NULL,  -- the window period end (== the snapshot stat_date M)

    action          SMALLINT     NOT NULL,  -- label code (stats.label_dim 'action'): 0 buy (bottom — reversal up) | 1 sell (top — reversal down)
    signal_threshold NUMERIC(14,6),         -- the strategy's breach bar in the value's own space: mov_rsi the side's own percentile bar (bottom = the window's q(p/100), top = q(1-p/100) — the {pct}pct sub_type names the tail mass the bar isolates)
    scale_method     TEXT,                   -- the confidence-scale curve family over the breach excess: 'linear' (the current build's only method; (linear, log, exponential) reserved)
    scale_base       NUMERIC(8,6),           -- the scale factor at zero excess (the signal exactly on the bar; default 1.0)
    scale_factor     NUMERIC(8,6),           -- the per-unit-excess coefficient the scale applies beyond the bar (linear: the slope)
    confidence_base  NUMERIC(8,6),           -- the on-bar confidence the scale starts from (the bucket's sign-aligned blended dir_ave); the breach confidence = ROUND(10000 x confidence_base x scale(|excess|))
    reason          TEXT,                   -- human-readable explanation of the strategy
    is_active       BOOLEAN      NOT NULL DEFAULT FALSE,  -- TRUE on the (code, sec_type, signal_type, signal_sub_type)'s LATEST end_date (refreshed after every run) — the live tier's current threshold set

    CONSTRAINT pk_signal_thresholds PRIMARY KEY (code, sec_type, signal_type, signal_sub_type, side, regime_state, start_date, end_date),
    CONSTRAINT chk_signal_thresholds_action CHECK (action BETWEEN 0 AND 1),
    CONSTRAINT chk_signal_thresholds_regime CHECK (regime_state BETWEEN 0 AND 3),
    CONSTRAINT chk_signal_thresholds_sec_type CHECK (sec_type BETWEEN 0 AND 2)
) PARTITION BY HASH (code);

SELECT public.create_hash_partitions('analysis_signals', 'signal_thresholds', 16);

-- Period-first lookup (UI / active-set fetches by end_date).
CREATE INDEX IF NOT EXISTS idx_signal_thresholds_end_date
    ON analysis_signals.signal_thresholds (sec_type, signal_type, end_date);


-- ----------------------------------------------------------------------------
--  Comments
-- ----------------------------------------------------------------------------
COMMENT ON TABLE analysis_signals.signal_thresholds IS 'Signal THRESHOLDS computed in-engine (the forecast layer was removed 2026-10-05): one row per in-engine bucket whose blended MIXED stats pass the plain gate (sign-aligned blended mean forward change > its side''s bar — buy 0.75% / sell 0%, GATE_DIR_AVE_MIN / GATE_DIR_AVE_MIN_SELL; the sign alignment applied in the Python emit layer). A threshold covers the bucket''s window period (start_date .. end_date = the snapshot stat_date; the trailing 10-year window) and stores the breach bar in the underlying value''s own space plus the confidence-scale parameters (confidence_base + scale_method/scale_base/scale_factor — the breach depth amplifies the base). Populated incrementally by python -m analysis.analysis_signals (mov_rsi pct = 1/2/3; both sides; EVERY regime split of a bucket — regime_state is a PK member, each split registers on its own gate pass).';
COMMENT ON COLUMN analysis_signals.signal_thresholds.sec_type IS 'Security type as its stats.label_dim (''sec_type'' domain) SMALLINT code: 0 = etf (ETF), 1 = index (CSI-style index), 2 = stock (individual equity).';
COMMENT ON COLUMN analysis_signals.signal_thresholds.code IS 'Ticker. ETFs bare 6-digit (e.g. "510050"); indices bare code (e.g. "000300"); stocks keep their exchange suffix (e.g. "600008.SS").';
COMMENT ON COLUMN analysis_signals.signal_thresholds.signal_type IS 'Detection family as its stats.label_dim (''signal_type'' domain) SMALLINT code: 0 = mov_rsi (RSI extreme-percentile strategy), 1 = bollinger (fixed-σ Bollinger band-breach strategy — the excess σ-depth over the 2 / 2.5 / 3σ bands replaces the percentile axis; renamed from boll_std + converted to the label code 2026-10-10, the value source computed in-engine off the fetched prices).';
COMMENT ON COLUMN analysis_signals.signal_thresholds.signal_sub_type IS 'Indicator + window + width: rsi{W}_{pct}pct for mov_rsi (W = the RSI window, pct = the emitted percentile width 1/2/3 — "1pct"/"2pct"/"3pct") or boll{W}_{k}std for bollinger (W = the Bollinger window, k = the σ multiplier — "2std"/"2p5std"/"3std", 2σ the classic default). The width rides IN the sub_type so each (window, width, side) is its own threshold and the live rows name their bar width.';
COMMENT ON COLUMN analysis_signals.signal_thresholds.is_ohlcv_boosted IS 'The bucket''s day-0 ohlcv-boost verdict (TRUE = boosted): over the bucket''s trigger anchors the engines/ohlcv_boost score (the day-0 OHLC pattern label + the (vol_state, px_speed) trading-amount/price-speed cell + the RSI tail depth past the bar, fused as score_bp = beta_ohlc*e_ohlc + beta_vol*e_vol + beta_rsi*e_rsi + gamma*e_ohlc*e_vol/100 with per-side measured ladders) has mean >= the side''s bar. An analysis marker on the SAME row (no separate boosted population — the retired forecast-layer cohorts are not revived); NEVER a gate: detection, the threshold bar, confidence and is_active are computed exactly as before.';
COMMENT ON COLUMN analysis_signals.signal_thresholds.side IS 'The bucket''s own side, STORED (no CASE derivation downstream) as its stats.label_dim (''side'' domain) SMALLINT code — 0 top, 1 bottom (mov_rsi: the RSI top/bottom-pct% percentile tail). Decode at the API boundary only.';
COMMENT ON COLUMN analysis_signals.signal_thresholds.regime_state IS 'The bucket''s market-regime split, STORED like side, as its stats.label_dim (''regime'' domain) SMALLINT code (0 calm / 1 hot / 2 panic / 3 quiet — the stats.market_regimes day label carried by the bucket''s trigger days). Each regime split passes the gate on its OWN blended forward profile and registers its own bar/confidence. PK member (every split of one config coexists); the live tier''s fetch_active_signals returns all, each self-contained (the strongest-breach dedup arbitrates same-bar collisions).';
COMMENT ON COLUMN analysis_signals.signal_thresholds.start_date IS 'The window period start: the snapshot key''s trailing-window first day (stat_date - 10 years + 1 day).';
COMMENT ON COLUMN analysis_signals.signal_thresholds.end_date IS 'The window period end: the snapshot stat_date M the threshold was derived from (the ROLLING LATEST key IS the sec_type''s latest available RSI data date).';
COMMENT ON COLUMN analysis_signals.signal_thresholds.action IS 'Trading action implied by the side, as its stats.label_dim (''action'' domain) SMALLINT code: 1 sell for top extremes (overbought RSI — the measured reversal is downward), 0 buy for bottom extremes (oversold RSI — the measured reversal is upward).';
COMMENT ON COLUMN analysis_signals.signal_thresholds.signal_threshold IS 'The strategy''s breach bar in the underlying value''s own space: for mov_rsi the side''s own PERCENTILE bar — bottom = the window''s q(p/100), top = q(1-p/100) interpolated quantile (the classic percentile construction: the {pct}pct sub_type names the tail mass the bar isolates; restored 2026-10-07 — the 2026-10-05 symmetric-in-space median-centered band landed one or both bars outside a bounded high-variance indicator''s reach and starved the bucket population below the sample floors) (recovered as the rsi value at the window-end trigger minus its stored trigger excess — constant per bucket). The live tier compares the CURRENT RSI value against this bar directly (sell breaches above, buy below).';
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
--  Per (code, audit_window, side, regime_state, snapshot): the fitted
--  shape of the sign-aligned blended forward response over the Gaussian
--  depth score s = Phi^-1(1 - q), where q is the day's tail probability
--  (the interpolated plotting-position percentile rank of the day's RSI
--  within the snapshot window). The population (the 2026-10-07
--  recovered is_typical split): the TAIL-EPISODE days of ONE pass per
--  row — is_typical TRUE = the first-touch passes' days, FALSE = the
--  re-entry passes' days (mov_ave_rsi.is_typical_{W}days attributes
--  every in-band day — registrations AND gap-1 continuations — to its
--  pass, so each pass covers the deep tail), each day weighted
--  1/episode_size so the response counts EPISODES, not days —
--  registration-only populations sit at the band edge by construction
--  and leave the deep tail (q <= 10%) unmeasured (the 2026-10-06 tails
--  study, 10 study indices).
--
--      curve(s) = mu_center + interp(fit_curve, s)
--
--    n            the cell's in-band day count (raw sample)
--    n_episodes   the cell's EPISODE count (the effective sample:
--                 each episode's days carry weight 1/size, summing to
--                 exactly 1 per episode)
--    mu_center    y-bar: the cell's episode-weighted sign-aligned
--                 blended mean forward change AT its own depth center
--    s_center     s-bar: the cell's own episode-weighted mean depth
--                 score (computed on demand per cell)
--    fit_curve    the pooled (index_class, side, regime) WEIGHTED
--                 ISOTONIC shape as {s: offset} at the 25 1%-band-
--                 center knot depths (continuous monotone under linear
--                 interpolation), offsets relative to the shape at
--                 s_center —
--                 significance-gated (|fit_t| >= CURVE_SLOPE_MIN_T,
--                 else flat at mu_center); direction follows the
--                 pool's rho sign, the buy side keeping the >= 0 guard (a negative buy rho stores flat)
--    tail_bars    the (code, window, side)'s per-side percentile RSI
--                 bars (bottom = the window's q(p/100), top =
--                 q(1-p/100) — the threshold tier's construction) at
--                 CURVE_TAIL_PCTS (1/2/3/5/10/15/20/25) —
--                 the denormalized s -> RSI-value mapping ({"1": bar,
--                 ...}); each row is self-contained (the family's
--                 denormalization rule)
--    s_lo/s_hi    the cell's MEASURED s range — consumers draw the fit
--                 only inside it (no extrapolation); the empirical
--                 band_curve dots carry the picture outside
--
--  Curves are the CONFIDENCE MODEL, not a strategy: no strategy gate, no
--  is_active (consumers resolve the LATEST end_date per key); every
--  cell with n >= the emit floor registers.
-- ----------------------------------------------------------------------------
CREATE TABLE analysis_signals.signal_curves (
    code            TEXT         NOT NULL,  -- ticker (etf "510050" / index "000300" / stock)
    sec_type        SMALLINT     NOT NULL,  -- label code (stats.label_dim 'sec_type'): 0=etf 1=index 2=stock
    signal_type     SMALLINT     NOT NULL,  -- label code (stats.label_dim 'signal_type'): 0=mov_rsi 1=bollinger — the detection family
    audit_window    SMALLINT     NOT NULL,  -- the curve's window W, the family's OWN window axis carried generically (mov_rsi the RSI window, bollinger the Bollinger window; no pct axis — the curve spans the whole tail)
    side            SMALLINT     NOT NULL,  -- label code (stats.label_dim 'side'): 0 top | 1 bottom — STORED so consumers never derive it
    regime_state    SMALLINT     NOT NULL DEFAULT 0,  -- label code (stats.label_dim 'regime'): the cell's market-regime split (0 calm / 1 hot / 2 panic / 3 quiet)

    start_date      DATE         NOT NULL,  -- the window period start (the snapshot key's trailing-window first day)
    end_date        DATE         NOT NULL,  -- the window period end (== the snapshot stat_date M)
    is_ohlcv_boosted  BOOLEAN      NOT NULL DEFAULT FALSE, -- the cell's population VARIANT (PK member): FALSE = the plain fit, TRUE = the boost twin (the same in-band days, weight-blended by their day-0 ohlcv score — engines/ohlcv_boost)
    is_typical        BOOLEAN      NOT NULL DEFAULT TRUE, -- FALSE if not usually observed for a signal, e.g., RSI re-entry is registered; TRUE if typical, e.g., RSI isolated ups/downs — the episode-PASS population (PK member, recovered 2026-10-07 from the pre-2026-10-06 boolean, replacing the population 0/1/2 smallint; SIDE-MIRRORED pass semantics — the amplified tail event differs by side): BUY TRUE = the isolated first-touch passes' days (> 20 trading days out of the tail, + continuations — the capitulation) / SELL TRUE = the re-test passes' days (a fresh episode whose previous overbought episode ended <= 20 days ago — the double top) / FALSE = the noisy population (flicker re-qualifications, falling knives, isolated breakouts; mov_ave_spread steps/rsi_typical RSI_TYPICAL_ISOLATED_DAYS)

    n               INTEGER      NOT NULL,  -- the cell's in-band day count over the window (the fit's raw sample; the population is EVERY tail-episode day — registrations + gap-1 continuations)
    n_episodes      INTEGER      NOT NULL,  -- the cell's EPISODE count (the fit's effective sample: each episode contributes weight exactly 1 via the 1/ep_size day weights)
    mu_center       NUMERIC(10,8) NOT NULL, -- y-bar: the cell's episode-weighted sign-aligned blended mean forward change at its own depth center (buy > 0 = rebound; sell sign-aligned the same way — can be negative)
    s_center        NUMERIC(8,6)  NOT NULL, -- s-bar: the cell's own episode-weighted mean depth score Phi^-1(1-q) of its in-band days (empirical, computed on demand)
    fit_curve       JSONB        NOT NULL,  -- the pooled (index_class, side, regime) WEIGHTED ISOTONIC shape as {"s": offset} at the 25 1%-band-center knot depths (continuous monotone under linear interpolation — the 2026-10-07 iso_knot representation), offsets RELATIVE to the shape at s_center: curve(s) = mu_center + interp(fit_curve, s). SIGNIFICANCE-GATED: all-zero offsets when the pool's |fit_t| < CURVE_SLOPE_MIN_T (a non-significant shape is band noise — the cell draws flat at its mu_center); the direction follows the pool's rho sign (a NEGATIVE buy rho stores the flat payload — the retired linear fit's >= 0 guard)
    fit_rho         NUMERIC(8,6) NOT NULL, -- the pooled weighted Spearman rho(s, y) — the shape's direction + strength (denormalized gate input)
    fit_t           NUMERIC(10,4) NOT NULL, -- the cluster-adjusted significance t = rho * sqrt((n_ep - 2) / (1 - rho^2)) with n_ep = the pool's EPISODE count (within-episode days are correlated — the effective sample is episodes, not days); fit_curve is gated flat when |fit_t| < CURVE_SLOPE_MIN_T
    s_lo            NUMERIC(8,6)  NOT NULL, -- the cell's shallowest MEASURED depth score (the min s of its in-band days) — the fitted shape is only drawn within [s_lo, s_hi]; outside it the empirical band dots carry the picture (no extrapolation)
    s_hi            NUMERIC(8,6)  NOT NULL, -- the cell's deepest MEASURED depth score (the max s of its in-band days)
    mu_1d           NUMERIC(10,8), -- the cell's episode-weighted sign-aligned mean NEXT-DAY forward change over its in-band days (finite legs only; NULL when no day carries the leg) — the path behind the blend's 0.10 next leg
    mu_3d           NUMERIC(10,8), -- the cell's episode-weighted sign-aligned mean 3-DAY forward change over its in-band days (finite legs only) — the path behind the blend's 0.15 3d leg
    mu_5d           NUMERIC(10,8), -- the cell's episode-weighted sign-aligned mean 5-DAY forward change over its in-band days (finite legs only) — the path behind the blend's dominant 0.50 5d leg
    mu_10d          NUMERIC(10,8), -- the cell's episode-weighted sign-aligned mean 10-DAY forward change over its in-band days (finite legs only) — the path behind the blend's 0.15 10d leg
    mu_20d          NUMERIC(10,8), -- the cell's episode-weighted sign-aligned mean 20-DAY forward change over its in-band days (finite legs only) — the path behind the blend's 0.10 20d leg
    tail_bars       JSONB        NOT NULL,  -- the (code, window, side)'s per-side percentile RSI bars (bottom = the window's q(p/100), top = q(1-p/100)) at pct 1/2/3/5/10/15/20/25: {"1": bar, ..., "25": bar} — the s -> RSI-value mapping (self-contained row)
    band_curve      JSONB,                  -- the cell's EMPIRICAL depth response at 1% stride over the whole tail: {"1": episode-weighted mean y of days with q in (0%,1%], ..., "25": ... } (sign-aligned blend; band key = the band's deeper edge pct; absent key = empty band) — the measured shape beside the isotonic fit; the 2026-10-06 population change fills the deep bands (every in-band day, not registrations only)

    created_at      TIMESTAMP    NOT NULL DEFAULT NOW(),

    CONSTRAINT pk_signal_curves PRIMARY KEY (code, sec_type, signal_type, audit_window, side, regime_state, start_date, end_date, is_ohlcv_boosted, is_typical),
    CONSTRAINT chk_signal_curves_side CHECK (side BETWEEN 0 AND 1),
    CONSTRAINT chk_signal_curves_regime CHECK (regime_state BETWEEN 0 AND 4),  -- 4 = the POOLED all-regime population
    CONSTRAINT chk_signal_curves_sec_type CHECK (sec_type BETWEEN 0 AND 2)
) PARTITION BY HASH (code);

SELECT public.create_hash_partitions('analysis_signals', 'signal_curves', 16);

-- Period-first lookup (UI curve fetches by end_date).
CREATE INDEX IF NOT EXISTS idx_signal_curves_end_date
    ON analysis_signals.signal_curves (sec_type, signal_type, end_date);

COMMENT ON TABLE analysis_signals.signal_curves IS 'Signal CURVES — the continuous depth-response model emitted beside signal_thresholds (python -m analysis.analysis_signals, the families'' curve/ subpackages): per (code, audit_window, side, regime_state, snapshot) curve(s) = mu_center + interp(fit_curve, s) over the Gaussian depth score s — mov_rsi reads s = Phi^-1(1-q) of the day''s percentile-rank tail probability, bollinger reads s = |z| DIRECTLY (the Gaussian tail of |z| gives Phi^-1(1-q) == |z|). POPULATION (the 2026-10-07 recovered is_typical split): each cell emits the TAIL-EPISODE days of ONE pass — is_typical TRUE = the first-touch passes'' days, FALSE = the re-entry passes'' days (the analysis layer''s is_typical flags attribute every in-band day — registrations AND gap-1 continuations — to its pass, so each pass covers the deep tail) — each day weighted 1/episode_size so the fit counts EPISODES, not days; a pass cell below the episode floor does not emit. mu_center is the cell''s own episode-weighted sign-aligned blended mean forward change, s_center its own weighted mean depth; fit_curve the WEIGHTED ISOTONIC shape of the TWO-TIER pool selection (2026-10-07): the CODE''s OWN pool when it carries >= CURVE_OWN_POOL_MIN_EPISODES (4) episodes and clears |fit_t| >= 2 on its own — the class pooling exists only to buy statistical significance for thin codes, so it stays OPTIONAL — else the pooled (index_class, side, regime, is_typical) shape (the code''s own stats.sec_classification industry_id pool — 2026-10-07: the L2 industry_id, not the L1 sector_id) — direction follows the selected pool''s rho sign, the buy side keeping its >= 0 guard, significance-gated (|fit_t| >= 2 with the cluster-adjusted t over the SELECTED pool''s EPISODE count, else flat at mu_center). tail_bars carries the interpolated RSI bars at 1/2/3/5/10/15/20/25 pct — the s -> RSI-value mapping, one self-contained row; band_curve the episode-weighted empirical 1%-band means (the deep bands are MEASURED since the population change). The 2026-10 depth-response study (10 study indices): the response is a deep-tail reversion STEP at q <= 1-2% over a shallow plateau, not a probit-linear gradient; sell depth structure is flat within the big-index classes. No gate / no is_active — consumers resolve the LATEST end_date per key and draw the fit only within [s_lo, s_hi] (no extrapolation).';
COMMENT ON COLUMN analysis_signals.signal_curves.is_typical IS 'FALSE if not usually observed for a signal, e.g., RSI re-entry is registered; TRUE if typical, e.g., RSI isolated ups/downs. The cell''s episode-PASS population (PK member), SIDE-MIRRORED — the amplified tail event differs by side (equity asymmetry: sharp falls revert, sharp rises continue): TRUE on the BUY side = the ISOLATED first-touch passes'' days (an episode whose first tail day comes > RSI_TYPICAL_ISOLATED_DAYS (20) trading days after the previous same-side band day — the capitulation after a month of calm — plus its gap-1 continuations; the 2026-10-07 start-gap study: buy-side rebounds rise monotonically with absence, -0.55% blended at gap 7-10 vs +2.36% at 31-60); TRUE on the SELL side = the RE-TEST passes'' days (a fresh episode whose previous overbought episode ended within the 20-day window — the double top, the amplified sell; W=14 ep-weighted +2.28% vs +1.21%); FALSE = the noisy population (flicker re-qualifications within the 5-day exit window, buy-side continuation starts — falling knives — and sell-side isolated breakouts, momentum ignitions after calm where the sell backfires). The analysis layer''s is_typical flags (mov_ave_spread steps/rsi_typical.py) attribute every in-band day to its pass, so each pass''s population INCLUDES the deep continuation days. Each cell emits both passes x the is_ohlcv_boosted variants; a pass-population cell below the CURVE_MIN_TRIGGERS episode floor does not emit.';
COMMENT ON COLUMN analysis_signals.signal_curves.audit_window IS 'The curve''s window W — the family''s own window axis carried generically (renamed from rsi_window 2026-10-10): mov_rsi the RSI window (3/6/10/14/20/60), bollinger the Bollinger window (5/20/60). No width axis — the curve spans the whole tail continuously (mov_rsi the q <= 25% band, bollinger the |z| >= 0.6745σ episode band; the threshold table''s discrete bars are points ON this curve).';
COMMENT ON COLUMN analysis_signals.signal_curves.is_ohlcv_boosted IS 'The cell''s population VARIANT (PK member): FALSE = the plain fit (episode weights), TRUE = the BOOST twin — the SAME in-band days with every statistic (mu_center, s_center, the mu_{n}d legs, the pooled shape + band_curve) weight-blended by each day''s episode weight TIMES its day-0 ohlcv score (engines/ohlcv_boost, w = max(1 + score/K, floor)); n, n_episodes and tail_bars are shared. The UI''s OHLCV-boost toggle picks which variant the curve overlay renders.';
COMMENT ON COLUMN analysis_signals.signal_curves.n IS 'The cell''s in-band day count over the window (every mov_ave_rsi.typical_ep_size_{W}days day: registrations + gap-1 continuations). The fit''s RAW sample; the effective sample is n_episodes (within-episode days are correlated). Cells under the emit floor never register.';
COMMENT ON COLUMN analysis_signals.signal_curves.n_episodes IS 'The cell''s EPISODE count = round(sum of the 1/ep_size day weights): each episode contributes total weight exactly 1, so this is the cluster-adjusted effective sample (the number of independent tail episodes behind the cell''s fit). The pooled gate''s t uses the POOL''S episode count the same way.';
COMMENT ON COLUMN analysis_signals.signal_curves.mu_center IS 'y-bar: the cell''s episode-weighted sign-aligned blended mean forward change (the 0.10/0.15/0.50/0.15/0.10 next/3d/5d/10d/20d mix, renormalized over valid legs — identical to the threshold tier''s dir_ave unit) evaluated AT the cell''s own depth center. curve(s_center) == mu_center exactly.';
COMMENT ON COLUMN analysis_signals.signal_curves.s_center IS 's-bar: the cell''s own episode-weighted mean depth score s = Phi^-1(1-q) over its in-band days (q = the day''s interpolated percentile rank within the window, the (rank+0.5)/n plotting position). Empirical per cell per snapshot — never a universal constant.';
COMMENT ON COLUMN analysis_signals.signal_curves.fit_curve IS 'The fitted depth shape as {"s": offset} — TWO-TIER pool selection (2026-10-07): the CODE''s OWN pool (its windows x days points) when it carries >= CURVE_OWN_POOL_MIN_EPISODES (4) episodes AND |fit_t| >= CURVE_SLOPE_MIN_T on its own — the (industry-class) pooling exists only to buy statistical significance for thin codes, so it stays OPTIONAL — else the pooled (index_class, side, regime_state, is_typical) pool (the code''s own stats.sec_classification industry_id — 2026-10-07: the L2 industry_id, not the L1 sector_id). The shape is the selected pool''s episode-weighted PAVA (isotonic regression) over its (s, y) points, sampled on the family s grid; its DIRECTION follows the pool''s weighted Spearman sign (a negative pool draws a nonincreasing shape — the study''s per-code reversals); the buy side keeps the retired linear fit''s >= 0 guard — a code''s own significant NEGATIVE buy rho stores its own flat payload (the response runs against the side; borrowing the class shape would overwrite the code''s own contrary evidence). SIGNIFICANCE-GATED: all-zero offsets when the selected pool''s |fit_t| < CURVE_SLOPE_MIN_T (2.0) — a non-significant shape summarizes band noise, not a depth response. Offsets RELATIVE to the shape at the cell''s s_center, so curve(s) = mu_center + interp(fit_curve, s) reproduces the fit exactly. Draw only within [s_lo, s_hi] — beyond the measured range the shape is not extrapolated; the empirical band_curve dots carry the picture.';
COMMENT ON COLUMN analysis_signals.signal_curves.fit_rho IS 'The SELECTED pool''s weighted Spearman rho(s, y) over its in-band points (weights = the 1/ep_size episode weights) — the shape''s direction and strength, denormalized beside fit_t so each row explains its own gate outcome. The selected pool is the code''s OWN pool when decisive on its own (>= 4 episodes and |t| >= 2), else the (industry_class, side, regime, pass) pool — see fit_curve.';
COMMENT ON COLUMN analysis_signals.signal_curves.fit_t IS 'The cluster-adjusted significance t of the SELECTED pool''s shape: t = rho * sqrt((n_ep - 2) / (1 - rho^2)) with n_ep = that pool''s episode count (sum of its 1/ep_size weights — within-episode days are correlated through overlapping forward windows, so the effective sample is episodes, not days). fit_curve stores the shape exactly when |fit_t| >= CURVE_SLOPE_MIN_T (2.0), else flat.';
COMMENT ON COLUMN analysis_signals.signal_curves.s_lo IS 'The cell''s shallowest MEASURED depth score (min s of its in-band days). The fitted shape is drawn only within [s_lo, s_hi]; outside it is unmeasured — no extrapolation (the registration-only predecessor effectively measured only [s_lo, ~1.7] and drew a line to 2.6).';
COMMENT ON COLUMN analysis_signals.signal_curves.s_hi IS 'The cell''s deepest MEASURED depth score (max s of its in-band days) — with the all-episode population this reaches the measured deep tail (s up to ~3+, q under 0.5%) that registration-only populations never sampled.';
COMMENT ON COLUMN analysis_signals.signal_curves.mu_1d IS 'The cell''s episode-weighted sign-aligned mean NEXT-DAY forward change over its in-band days (bottom as-is, top negated — the y_dir convention), averaged over the days whose 1d leg is finite; NULL when no day carries the leg. The raw path behind the blend''s 0.10 next leg — mu_center is the renormalized blend of these legs, NOT their weighted mean (the two coincide only when every leg is finite on every day).';
COMMENT ON COLUMN analysis_signals.signal_curves.mu_3d IS 'The cell''s episode-weighted sign-aligned mean 3-DAY forward change over its in-band days (finite legs only; NULL when none) — the path behind the blend''s 0.15 3d leg.';
COMMENT ON COLUMN analysis_signals.signal_curves.mu_5d IS 'The cell''s episode-weighted sign-aligned mean 5-DAY forward change over its in-band days (finite legs only; NULL when none) — the path behind the blend''s dominant 0.50 5d leg.';
COMMENT ON COLUMN analysis_signals.signal_curves.mu_10d IS 'The cell''s episode-weighted sign-aligned mean 10-DAY forward change over its in-band days (finite legs only; NULL when none) — the path behind the blend''s 0.15 10d leg.';
COMMENT ON COLUMN analysis_signals.signal_curves.mu_20d IS 'The cell''s episode-weighted sign-aligned mean 20-DAY forward change over its in-band days (finite legs only; NULL when none) — the path behind the blend''s 0.10 20d leg; the most NULL-prone (right-edge days near the rolling snapshot have no 20d leg yet).';
COMMENT ON COLUMN analysis_signals.signal_curves.band_curve IS 'The cell''s EMPIRICAL depth response at 1% stride over the whole q <= 25% tail: {k: episode-weighted mean of the sign-aligned blended response of the days whose tail probability q falls in ((k-1)%, k%]} for k = 1..25 (band key = the band''s deeper edge pct; absent key = no day in that band). The measured shape drawn beside the isotonic fit. The 2026-10-06 population change (every tail-episode day, episode-weighted) fills the deep bands the registration-only population left empty — the study''s +4% reversion step at (0,1%] is now measured, not smeared.';
COMMENT ON COLUMN analysis_signals.signal_curves.tail_bars IS 'The (code, rsi_window, side)''s per-side PERCENTILE RSI bars at tail pct 1/2/3/5/10/15/20/25 as {"1": bar, ...}: bar(pct) = the side''s own interpolated tail quantile (bottom = the window''s q(p/100), top = q(1-p/100) — the classic percentile construction; restored 2026-10-07 with the threshold tier''s bars, so the two tiers agree exactly at the shared pcts 1/2/3). The denormalized s -> RSI-value mapping so each row is self-contained (the family''s denormalization rule) — consumers plot mu against RSI values or overlay the threshold bars without a second lookup.';


CREATE TABLE analysis_signals.history_signals (
    code            TEXT          NOT NULL,  -- ticker (etf "510050" / index "000300" / stock)
    sec_type        SMALLINT      NOT NULL,  -- label code (stats.label_dim 'sec_type'): 0=etf 1=index 2=stock
    signal_type     SMALLINT      NOT NULL,  -- label code (stats.label_dim 'signal_type'): 0=mov_rsi 1=bollinger — the strategy's detection family
    signal_sub_type TEXT          NOT NULL,  -- 'rsi6_1pct'..'rsi60_3pct' — the strategy this event belongs to
    is_ohlcv_boosted BOOLEAN     NOT NULL DEFAULT FALSE,  -- the event's population VARIANT (PK member): FALSE = plain, TRUE = the boost twin (same trigger, w-scaled confidence — engines/ohlcv_boost)
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
    is_typical      BOOLEAN,  -- the trigger day's episode-PASS attribution (the rsi analysis layer's is_typical_{W}days at the day's own window — 2026-10-07): TRUE = the typical pass (buy: the isolated first touch; sell: the re-test), FALSE = the re-entry pass (flicker re-crossings / continuations / isolated breakouts — the noisy population). NULL = unclassified (rows written before the stamp, unrebuilt sec_types). Attribute, not a variant: the event is the event; the pass is a property of its day — the UI's history pass toggle filters on it.

    created_at      TIMESTAMP     NOT NULL DEFAULT NOW(),  -- record insertion time

    CONSTRAINT pk_history_signals PRIMARY KEY (code, sec_type, signal_type, signal_sub_type, date, time, is_ohlcv_boosted),
    CONSTRAINT chk_history_signals_action CHECK (action BETWEEN 0 AND 1),
    CONSTRAINT chk_history_signals_regime CHECK (regime_state BETWEEN 0 AND 3),
    CONSTRAINT chk_history_signals_sec_type CHECK (sec_type BETWEEN 0 AND 2)
) PARTITION BY HASH (code);

SELECT public.create_hash_partitions('analysis_signals', 'history_signals', 16);

-- Date-first lookup (UI / "signals on day X for sec_type Y").
CREATE INDEX IF NOT EXISTS idx_history_signals_date
    ON analysis_signals.history_signals (sec_type, signal_type, date);

-- ----------------------------------------------------------------------------
--  Comments
-- ----------------------------------------------------------------------------
COMMENT ON TABLE analysis_signals.history_signals IS 'History SIGNAL EVENTS: one row per qualifying trigger day of an analysis_signals.signal_thresholds strategy — the day''s indicator value, the bar it crossed and the excess (a structural clone of live.live_signals so the two tiers read identically). Only dates INSIDE the strategy''s own snapshot M are recorded (one snapshot owns each date → no cross-snapshot PK conflicts). Populated by python -m analysis.analysis_signals together with the threshold rows (same snapshot transaction); mov_rsi pct = 1/2/3 only.';
COMMENT ON COLUMN analysis_signals.history_signals.sec_type IS 'Security type as its stats.label_dim (''sec_type'' domain) SMALLINT code: 0 = etf (ETF), 1 = index (CSI-style index), 2 = stock (individual equity).';
COMMENT ON COLUMN analysis_signals.history_signals.code IS 'Ticker. ETFs bare 6-digit (e.g. "510050"); indices bare code (e.g. "000300"); stocks keep their exchange suffix (e.g. "600008.SS").';
COMMENT ON COLUMN analysis_signals.history_signals.signal_type IS 'The strategy''s detection family as its stats.label_dim (''signal_type'' domain) SMALLINT code: 0 = mov_rsi, 1 = bollinger (renamed from boll_std 2026-10-10).';
COMMENT ON COLUMN analysis_signals.history_signals.signal_sub_type IS 'The strategy this event belongs to: rsi{W}_{pct}pct / boll{W}_{k}std (same formats as signal_thresholds.signal_sub_type).';
COMMENT ON COLUMN analysis_signals.history_signals.is_ohlcv_boosted IS 'The event''s population VARIANT (PK member): FALSE = the plain record (the historical row, unweighted), TRUE = the BOOST twin — the SAME trigger day with its confidence trust-weighted by the day-0 ohlcv score (ROUND(10000 x w x confidence_base x scale(|signal_excess|)), w = max(1 + score/K, floor) from the engines/ohlcv_boost ladders). Twins exist only where the (family, sec_type, side) carries a boost config; every other column is identical between the twins.';
COMMENT ON COLUMN analysis_signals.history_signals.date IS 'The trigger day — a bucket trigger date inside the strategy''s own snapshot M: each qualifying streak''s FIRST day (the moment the signal becomes observable).';
COMMENT ON COLUMN analysis_signals.history_signals.signal_delay_days IS 'The owning strategy''s entry delay, denormalized per event. Stays in the schema, written 0: the 0..5 delay ladder + the optimal-entry selection were REMOVED 2026-10-05 — the trigger IS the qualifying streak''s first day.';
COMMENT ON COLUMN analysis_signals.history_signals.time IS 'The bar time of the recorded breach: 15:00:00 (the day close) for history rows written by the emit pipeline.';
COMMENT ON COLUMN analysis_signals.history_signals.action IS 'The strategy''s action as its stats.label_dim (''action'' domain) SMALLINT code: 1 sell (top — the day crossed ABOVE its bar, signal_excess > 0) or 0 buy (bottom — crossed BELOW, signal_excess < 0).';
COMMENT ON COLUMN analysis_signals.history_signals.signal IS 'The day''s indicator value that crossed the bar, in the value''s own space (mov_rsi: the RSI value; boll_std: the σ-depth z = (price − SMA_W)/σ_W).';
COMMENT ON COLUMN analysis_signals.history_signals.signal_threshold IS 'The bar crossed: value at the trigger day minus its stored trigger excess (mov_rsi: the window''s percentile bar — constant per bucket).';
COMMENT ON COLUMN analysis_signals.history_signals.signal_excess IS 'signal - signal_threshold — the signed breach depth (> 0 above the bar, < 0 below).';
COMMENT ON COLUMN analysis_signals.history_signals.signal_excess_pct IS 'signal_excess / |signal_threshold| * 100 — the unitless breach depth pct; NULL when signal_threshold = 0.';
COMMENT ON COLUMN analysis_signals.history_signals.confidence IS 'The SCALED breach confidence on the INTEGER basis-point scale: ROUND(10000 x signal_thresholds.confidence_base x scale(|signal_excess|)) — the on-bar base (the sign-aligned blended dir_ave) amplified by the owning row''s scale model over the breach depth. (Rows written before the 2026-10-05 scale migration carry the flat ROUND(10000 x dir_ave) of the retired signal_strategies.confidence.)';
COMMENT ON COLUMN analysis_signals.history_signals.is_day_close_trigger IS 'TRUE = day-close history row (time 15:00:00, written by the emit pipeline); FALSE = intraday record (reserved — the live tier writes its own live.live_signals table, not this one).';
COMMENT ON COLUMN analysis_signals.history_signals.regime_state IS 'The trigger day''s market regime as its stats.label_dim (''regime'' domain) SMALLINT code (0 calm / 1 hot / 2 panic / 3 quiet — the stats.market_regimes day label looked up per (code, date)). Recorded, never a gate: the strategy''s bucket is the SAME regime''s split (PK-joined), so the event flags which regime produced it.';
COMMENT ON COLUMN analysis_signals.history_signals.is_typical IS 'The trigger day''s episode-PASS attribution (2026-10-07): the rsi analysis layer''s is_typical_{W}days flag at the day''s own window (mov_ave_spread steps/rsi_typical — SIDE-MIRRORED: the BUY side''s typical pass is the ISOLATED first touch > 20 traded days out of the tail, the capitulation; the SELL side''s is the RE-TEST, a fresh episode whose previous overbought episode ended within the window; FALSE = the re-entry pass — flicker re-crossings within the K=5 exit window, buy-side continuation starts, sell-side isolated breakouts — the noisy population; one episode''s days share its pass). TRUE = the same pass population the curve tier''s is_typical TRUE cells fit; the UI history panel''s pass toggle filters on it. NULL = unclassified (rows written before the stamp — rebuilt sec_types re-stamp on --force).';

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
