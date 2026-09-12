-- ============================================================================
--  Table: analysis_forecasts.pe_state
--
--  Tenth MOTIVATION (bucket-defining) table of the forecast analysis:
--  valuation STATE buckets over the PE series of analysis.pe (raw PE —
--  index PE from stats.index_valuation.pe, etf/stock PE pre-computed by
--  builds.etf / builds.stock; NULL on no-earnings / invalid-PE days →
--  no bucket there), standardized by the code's OWN trailing moments:
--
--    z = (pe - μ) / σ with μ/σ = the code's rolling
--        z_window-row (default 1220 = 5y of trading rows),
--        min_periods z_min_periods (default 250 non-NULL pe
--        observations) moments of the pe, SHIFTED 1 row
--        (no look-ahead — px_vol / margin_ratio convention).
--        Undefined where pe is NULL or the history is short
--        → no bucket.
--
--    val_state: vlow z <= vlow_bar   (default -2.0)
--               low  vlow_bar < z <= low_bar  (-2.0 / -1.0)
--               mid  low_bar  < z <= high_bar (central bulk — no claim)
--               high high_bar < z <= vhigh_bar (1.0 / 2.0)
--               vhigh z > vhigh_bar   (default +2.0)
--
--  SIDE (pe is LOWER the better — a high PE is an expensive, stretched
--  valuation, the family's defining reading):
--    vlow/low (cheap) = 'bottom' (bullish — reverse_prob = P(the n-day
--              forward window's path high > +threshold)),
--    high/vhigh (expensive) = 'top' (bearish — reverse_prob =
--              P(path low < -threshold)),
--    mid = 'flat' with reverse_prob NULL (no directional claim — the
--              central bulk carries none, margin_ratio / px_vol
--              precedent). The dividend_yield sibling lives in
--              12_dividend_state.sql — same z bars, REVERSED side
--              mapping (the two families' "better" direction differs).
--
--  side is MATERIALIZED per row, so analysis_signals.gate and every
--  other consumer read the tables unchanged (dir_ave = -ave_change for
--  top, +ave_change for bottom).
--
--  Universe: index + etf + stock (NULL pe days simply form no bucket).
--
--  Buckets are STATE cells with STREAK-MERGE (2026-09 unified pipeline,
--  px_vol convention): consecutive grid rows holding the same val_state
--  collapse into ONE forecast signal anchored at the run's MID day —
--  the bucket's mean run length is recorded on
--  analysis_forecasts.forecast_identities.streak_signal_days — and the
--  bucket split is by PK member is_market_hyped exactly like the other
--  engines. Results live in analysis_forecasts.forecast_results via
--  forecast_id (1:N — 4 period rows next/5d/20d/60d).
--
--  Threshold columns are RECORDED BUILD PARAMETERS (NOT part of the
--  PK — rebuilding with different values requires --force). The
--  full-5y window gate is identical to the other engines.
--  CODE-CLUSTERED, forecast_id-keyed (2026-09 shape): PK (code,
--  forecast_id) on HASH (code) partitions — the code-clustered
--  read/write axis; forecast_id-only joins/searches use the secondary
--  idx_pe_state_forecast_id. code is the ONLY identity column
--  stored here (the partition key); sec_type and stat_month live ONLY
--  in analysis_forecasts.forecast_identities — the search table
--  (see 02_mov_rsi_mov_std.sql).
-- ============================================================================

CREATE TABLE IF NOT EXISTS analysis_forecasts.pe_state (
    code            TEXT         NOT NULL,  -- hash partition key + PK lead; sec_type / stat_month live in forecast_identities
    forecast_id     BIGINT      NOT NULL,  -- 1:N link to the bucket's 4 forecast_results period rows; id-only joins/searches use idx_pe_state_forecast_id
    val_state       TEXT         NOT NULL,  -- 'vlow' | 'low' | 'mid' | 'high' | 'vhigh' (z bars of the raw PE vs the code's own trailing moments)
    side            TEXT         NOT NULL,  -- pe LOWER the better: vlow/low (cheap) → 'bottom' (bullish), high/vhigh (expensive) → 'top' (bearish); mid → 'flat'

    -- Recorded build parameters (NOT PK — recorded for provenance; a
    -- rebuild with different values requires --force).
    z_window        INTEGER      NOT NULL DEFAULT 1220,  -- rolling μ/σ window of the pe (rows ≈ 5y of trading days)
    z_min_periods   INTEGER      NOT NULL DEFAULT 250,   -- min non-NULL pe observations in the window
    vlow_bar        NUMERIC(4,2) NOT NULL DEFAULT -2.00, -- vlow upper z-bar (z <= vlow_bar)
    low_bar         NUMERIC(4,2) NOT NULL DEFAULT -1.00, -- low upper z-bar (vlow_bar < z <= low_bar)
    high_bar        NUMERIC(4,2) NOT NULL DEFAULT 1.00,  -- high lower z-bar (high_bar < z <= vhigh_bar)
    vhigh_bar       NUMERIC(4,2) NOT NULL DEFAULT 2.00,  -- vhigh lower z-bar (z > vhigh_bar)
    lookback_period TEXT         NOT NULL DEFAULT '5y',  -- trailing window the bucket was computed over ('5y' = stat_month - 5y .. stat_month)

    -- motivation cols
    is_market_hyped BOOLEAN      NOT NULL,  -- ANY bucket date inside a mov_ave_market_hypes episode (any check-in period)

    CONSTRAINT pk_pe_state PRIMARY KEY (code, forecast_id),
    CONSTRAINT chk_pe_state_val_state
        CHECK (val_state IN ('vlow', 'low', 'mid', 'high', 'vhigh'))
) PARTITION BY HASH (code);

SELECT public.create_hash_partitions('analysis_forecasts', 'pe_state', 16);

CREATE INDEX IF NOT EXISTS idx_pe_state_forecast_id
    ON analysis_forecasts.pe_state (forecast_id);

-- ----------------------------------------------------------------------------
--  Comments
-- ----------------------------------------------------------------------------
COMMENT ON TABLE analysis_forecasts.pe_state IS 'Valuation state buckets (motivation) over the PE series of analysis.pe: one row per forecast_id — the window days of one security-month whose raw pe (index PE from stats.index_valuation.pe, etf/stock PE pre-computed by builds) sits in the named z state of the code''s OWN trailing distribution: z = (pe - μ)/σ with rolling-1220-row (min 250 non-NULL) moments shifted 1 row. States: vlow z<=-2 / low (-2,-1] / mid (-1,+1] / high (+1,+2] / vhigh z>2. SIDE (pe LOWER the better): vlow/low cheap states carry side ''bottom'' (bullish), high/vhigh expensive states ''top'' (bearish — reverse_prob = P(the forward window''s path low < -threshold)); mid = flat (NULL reverse_prob). The dividend_yield sibling lives in analysis_forecasts.dividend_state (same z bars, REVERSED side mapping). Streak-merged state signals (consecutive same-state days = ONE mid-anchored signal; mean run length on forecast_identities.streak_signal_days). Keyed by the surrogate forecast_id (hash partition key); the shared identity (sec_type, code, stat_month) + bucket family live in analysis_forecasts.forecast_identities. Results (forward changes / swing-aware reversal probabilities at the FIXED 1% bar) live in analysis_forecasts.forecast_results via forecast_id. Populated by python -m analyze.analysis_forecasts.';
COMMENT ON COLUMN analysis_forecasts.pe_state.forecast_id IS 'Surrogate PK + hash-partition key (1:N link to the bucket''s 4 period rows in analysis_forecasts.forecast_results, allocated by the writer, shared across all 4 periods). The bucket''s identity (sec_type, code, stat_month) + bucket family are registered in analysis_forecasts.forecast_identities under this id.';
COMMENT ON COLUMN analysis_forecasts.pe_state.val_state IS 'Valuation z state of the day: z = (pe - μ)/σ of the code''s rolling-1220-row (min 250 non-NULL pe observations) moments shifted 1 row: vlow z <= -2; low -2 < z <= -1; mid -1 < z <= +1; high +1 < z <= +2; vhigh z > +2. Undefined z (pe NULL — no-earnings / invalid-PE days — or short history) → no bucket.';
COMMENT ON COLUMN analysis_forecasts.pe_state.side IS 'Reversal side of the bucket''s forecast_results.reverse_prob — pe is LOWER the better (a high PE is an expensive, stretched valuation): vlow/low (cheap) = ''bottom'' (bullish — reversal counts n-day changes above +reverse_threshold), high/vhigh (expensive) = ''top'' (bearish — reversal below -reverse_threshold); mid = ''flat'' (no directional claim; reverse_prob NULL). Mirrors the mov_* / px_vol / margin_ratio side semantics so analysis_signals.gate consumes the table unchanged.';
COMMENT ON COLUMN analysis_forecasts.pe_state.z_window IS 'Recorded build parameter: rolling window (rows) of the pe moments μ/σ (default 1220 ≈ 5y of trading rows). Shifted 1 row before use (no look-ahead).';
COMMENT ON COLUMN analysis_forecasts.pe_state.z_min_periods IS 'Recorded build parameter: minimum non-NULL pe observations inside z_window for z to be defined (default 250).';
COMMENT ON COLUMN analysis_forecasts.pe_state.vlow_bar IS 'Recorded build parameter: vlow upper z-bar (default -2.0).';
COMMENT ON COLUMN analysis_forecasts.pe_state.low_bar IS 'Recorded build parameter: low upper z-bar (default -1.0).';
COMMENT ON COLUMN analysis_forecasts.pe_state.high_bar IS 'Recorded build parameter: high lower z-bar (default +1.0).';
COMMENT ON COLUMN analysis_forecasts.pe_state.vhigh_bar IS 'Recorded build parameter: vhigh lower z-bar (default +2.0).';
COMMENT ON COLUMN analysis_forecasts.pe_state.lookback_period IS 'Recorded build parameter (NOT a PK member): the trailing calendar window the bucket was computed over — ''5y'' = (stat_month - 5 years, stat_month]. Default ''5y''; a rebuild with a different lookback requires --force.';
COMMENT ON COLUMN analysis_forecasts.pe_state.is_market_hyped IS 'TRUE when ANY of the bucket''s dates falls inside one of the code''s stats.mov_ave_market_hypes episodes (any min_checkin_period).';
