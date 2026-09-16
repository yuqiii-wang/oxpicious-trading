-- ============================================================================
--  Table: analysis_forecasts.dividend_state
--
--  Eleventh MOTIVATION (bucket-defining) table of the forecast analysis:
--  valuation STATE buckets over the dividend-yield series of
--  analysis.dividends (trailing-12m D/P, fractional; NULL for
--  non-payers → only paying codes form dividend buckets), standardized
--  by the code's OWN trailing moments:
--
--    z = (dividend_yield - μ) / σ with μ/σ = the code's rolling
--        z_window-row (default 1220 = 5y of trading rows),
--        min_periods z_min_periods (default 250 non-NULL yield
--        observations) moments of the yield, SHIFTED 1 row
--        (no look-ahead — px_vol / margin_ratio convention).
--        Undefined where the yield is NULL or the history is short
--        → no bucket.
--
--    val_state: vlow z <= vlow_bar   (default -2.0)
--               low  vlow_bar < z <= low_bar  (-2.0 / -1.0)
--               mid  low_bar  < z <= high_bar (central bulk — no claim)
--               high high_bar < z <= vhigh_bar (1.0 / 2.0)
--               vhigh z > vhigh_bar   (default +2.0)
--
--  SIDE (the yield is HIGHER the better — a high trailing yield is a
--  cheap, well-supported valuation; the REVERSE of the pe_state
--  family's mapping, 11_pe_state.sql):
--    vlow/low (low yield) = 'top' (bearish — reverse_prob = P(the n-day
--              forward window's path low < -threshold)),
--    high/vhigh (high yield) = 'bottom' (bullish — reverse_prob =
--              P(path high > +threshold)),
--    mid = 'flat' with reverse_prob NULL (no directional claim).
--
--  side is MATERIALIZED per row, so analysis_signals.gate and every
--  other consumer read the tables unchanged (dir_ave = -ave_change for
--  top, +ave_change for bottom).
--
--  Universe: index + etf + stock (non-payer days simply form no
--  bucket).
--
--  Buckets are STATE cells with STREAK-MERGE (2026-09 unified pipeline,
--  px_vol convention): consecutive grid rows holding the same val_state
--  collapse into ONE forecast signal anchored at the run's MID day —
--  the bucket's mean run length is recorded on
--  analysis_forecasts.forecast_identities.streak_signal_days — and the
--  bucket split is by PK member is_market_hyped exactly like the other
--  engines. Results live in analysis_forecasts.forecast_results via
--  forecast_id (1:N — 5 period rows next/5d/20d/60d/mixed).
--
--  Threshold columns are RECORDED BUILD PARAMETERS (NOT part of the
--  PK — rebuilding with different values requires --force). The
--  full-5y window gate is identical to the other engines.
--  CODE-CLUSTERED, forecast_id-keyed (2026-09 shape): PK (code,
--  forecast_id) on HASH (code) partitions — the code-clustered
--  read/write axis; forecast_id-only joins/searches use the secondary
--  idx_dividend_state_forecast_id. code is the ONLY identity column
--  stored here (the partition key); sec_type and stat_month live ONLY
--  in analysis_forecasts.forecast_identities — the search table
--  (see 02_mov_rsi_mov_std.sql).
-- ============================================================================

CREATE TABLE IF NOT EXISTS analysis_forecasts.dividend_state (
    code            TEXT         NOT NULL,  -- hash partition key + PK lead; sec_type / stat_month live in forecast_identities
    forecast_id     BIGINT      NOT NULL,  -- 1:N link to the bucket's 5 forecast_results period rows; id-only joins/searches use idx_dividend_state_forecast_id
    val_state       TEXT         NOT NULL,  -- 'vlow' | 'low' | 'mid' | 'high' | 'vhigh' (z bars of the dividend yield vs the code's own trailing moments)
    side            TEXT         NOT NULL,  -- yield HIGHER the better: vlow/low (low yield) → 'top' (bearish), high/vhigh (high yield) → 'bottom' (bullish); mid → 'flat'

    -- Recorded build parameters (NOT PK — recorded for provenance; a
    -- rebuild with different values requires --force).
    z_window        INTEGER      NOT NULL DEFAULT 1220,  -- rolling μ/σ window of the dividend_yield (rows ≈ 5y of trading days)
    z_min_periods   INTEGER      NOT NULL DEFAULT 250,   -- min non-NULL dividend_yield observations in the window
    vlow_bar        NUMERIC(4,2) NOT NULL DEFAULT -2.00, -- vlow upper z-bar (z <= vlow_bar)
    low_bar         NUMERIC(4,2) NOT NULL DEFAULT -1.00, -- low upper z-bar (vlow_bar < z <= low_bar)
    high_bar        NUMERIC(4,2) NOT NULL DEFAULT 1.00,  -- high lower z-bar (high_bar < z <= vhigh_bar)
    vhigh_bar       NUMERIC(4,2) NOT NULL DEFAULT 2.00,  -- vhigh lower z-bar (z > vhigh_bar)
    lookback_period TEXT         NOT NULL DEFAULT '5y',  -- trailing window the bucket was computed over ('5y' = stat_month - 5y .. stat_month)

    -- motivation cols
    is_market_hyped BOOLEAN      NOT NULL,  -- ANY bucket date inside a mov_ave_market_hypes episode (any check-in period)

    CONSTRAINT pk_dividend_state PRIMARY KEY (code, forecast_id),
    CONSTRAINT chk_dividend_state_val_state
        CHECK (val_state IN ('vlow', 'low', 'mid', 'high', 'vhigh'))
) PARTITION BY HASH (code);

SELECT public.create_hash_partitions('analysis_forecasts', 'dividend_state', 16);

CREATE INDEX IF NOT EXISTS idx_dividend_state_forecast_id
    ON analysis_forecasts.dividend_state (forecast_id);

-- ----------------------------------------------------------------------------
--  Migration (2026-09): the combined metric-keyed table
--  analysis_forecasts.pe_dividend_state (metric ∈ 'pe' | 'dividend_yield')
--  is SPLIT into the per-metric pe_state (11_pe_state.sql) +
--  dividend_state (this file) — each metric's rows move with their
--  forecast_id into its own table (no metric column anymore) and the
--  forecast_identities registry re-tags bucket 'pe_dividend_state' →
--  'pe_state' / 'dividend_state' per moved forecast_id. Registry rows
--  whose forecast_id did not move (cannot happen under the old NOT NULL
--  metric — defensive) are deleted before the old table drops. The
--  whole block is one transaction (DDL is transactional) guarded by the
--  old table's existence — idempotent, a no-op on fresh installs.
-- ----------------------------------------------------------------------------
DO $$
BEGIN
    IF to_regclass('analysis_forecasts.pe_dividend_state') IS NOT NULL THEN
        INSERT INTO analysis_forecasts.pe_state
               (code, forecast_id, val_state, side,
                z_window, z_min_periods,
                vlow_bar, low_bar, high_bar, vhigh_bar,
                lookback_period, is_market_hyped)
        SELECT  code, forecast_id, val_state, side,
                z_window, z_min_periods,
                vlow_bar, low_bar, high_bar, vhigh_bar,
                lookback_period, is_market_hyped
        FROM    analysis_forecasts.pe_dividend_state
        WHERE   metric = 'pe';

        INSERT INTO analysis_forecasts.dividend_state
               (code, forecast_id, val_state, side,
                z_window, z_min_periods,
                vlow_bar, low_bar, high_bar, vhigh_bar,
                lookback_period, is_market_hyped)
        SELECT  code, forecast_id, val_state, side,
                z_window, z_min_periods,
                vlow_bar, low_bar, high_bar, vhigh_bar,
                lookback_period, is_market_hyped
        FROM    analysis_forecasts.pe_dividend_state
        WHERE   metric = 'dividend_yield';

        UPDATE analysis_forecasts.forecast_identities i
           SET bucket = 'pe_state'
         WHERE i.bucket = 'pe_dividend_state'
           AND EXISTS (SELECT 1 FROM analysis_forecasts.pe_state p
                       WHERE p.forecast_id = i.forecast_id);

        UPDATE analysis_forecasts.forecast_identities i
           SET bucket = 'dividend_state'
         WHERE i.bucket = 'pe_dividend_state'
           AND EXISTS (SELECT 1 FROM analysis_forecasts.dividend_state d
                       WHERE d.forecast_id = i.forecast_id);

        DELETE FROM analysis_forecasts.forecast_identities
         WHERE bucket = 'pe_dividend_state';

        DROP TABLE analysis_forecasts.pe_dividend_state;
    END IF;
END $$;

-- ----------------------------------------------------------------------------
--  Comments
-- ----------------------------------------------------------------------------
COMMENT ON TABLE analysis_forecasts.dividend_state IS 'Valuation state buckets (motivation) over the dividend-yield series of analysis.dividends: one row per forecast_id — the window days of one security-month whose trailing-12m D/P (fractional) sits in the named z state of the code''s OWN trailing distribution: z = (dividend_yield - μ)/σ with rolling-1220-row (min 250 non-NULL) moments shifted 1 row. States: vlow z<=-2 / low (-2,-1] / mid (-1,+1] / high (+1,+2] / vhigh z>2. SIDE (yield HIGHER the better — the REVERSE of pe_state''s mapping): vlow/low low-yield states carry side ''top'' (bearish — reverse_prob = P(the forward window''s path low < -threshold)), high/vhigh high-yield states ''bottom'' (bullish); mid = flat (NULL reverse_prob). Non-payer days (NULL yield) form no bucket. The pe sibling lives in analysis_forecasts.pe_state (same z bars, REVERSED side mapping). Streak-merged state signals (consecutive same-state days = ONE mid-anchored signal; mean run length on forecast_identities.streak_signal_days). Keyed by the surrogate forecast_id (hash partition key); the shared identity (sec_type, code, stat_month) + bucket family live in analysis_forecasts.forecast_identities. Results (forward changes / swing-aware reversal probabilities at the FIXED 1% bar) live in analysis_forecasts.forecast_results via forecast_id. Populated by python -m analyze.analysis_forecasts.';
COMMENT ON COLUMN analysis_forecasts.dividend_state.forecast_id IS 'Surrogate PK + hash-partition key (1:N link to the bucket''s 5 period rows in analysis_forecasts.forecast_results, allocated by the writer, shared across all 5 periods). The bucket''s identity (sec_type, code, stat_month) + bucket family are registered in analysis_forecasts.forecast_identities under this id.';
COMMENT ON COLUMN analysis_forecasts.dividend_state.val_state IS 'Valuation z state of the day: z = (dividend_yield - μ)/σ of the code''s rolling-1220-row (min 250 non-NULL yield observations) moments shifted 1 row: vlow z <= -2; low -2 < z <= -1; mid -1 < z <= +1; high +1 < z <= +2; vhigh z > +2. Undefined z (yield NULL — non-payers — or short history) → no bucket.';
COMMENT ON COLUMN analysis_forecasts.dividend_state.side IS 'Reversal side of the bucket''s forecast_results.reverse_prob — the yield is HIGHER the better (a high trailing yield is a cheap, well-supported valuation): vlow/low (low yield) = ''top'' (bearish — reversal counts n-day changes below -threshold), high/vhigh (high yield) = ''bottom'' (bullish — reversal above +threshold); mid = ''flat'' (no directional claim; reverse_prob NULL). The REVERSE of the pe_state mapping. Mirrors the mov_* / px_vol / margin_ratio side semantics so analysis_signals.gate consumes the table unchanged.';
COMMENT ON COLUMN analysis_forecasts.dividend_state.z_window IS 'Recorded build parameter: rolling window (rows) of the dividend_yield moments μ/σ (default 1220 ≈ 5y of trading rows). Shifted 1 row before use (no look-ahead).';
COMMENT ON COLUMN analysis_forecasts.dividend_state.z_min_periods IS 'Recorded build parameter: minimum non-NULL dividend_yield observations inside z_window for z to be defined (default 250).';
COMMENT ON COLUMN analysis_forecasts.dividend_state.vlow_bar IS 'Recorded build parameter: vlow upper z-bar (default -2.0).';
COMMENT ON COLUMN analysis_forecasts.dividend_state.low_bar IS 'Recorded build parameter: low upper z-bar (default -1.0).';
COMMENT ON COLUMN analysis_forecasts.dividend_state.high_bar IS 'Recorded build parameter: high lower z-bar (default +1.0).';
COMMENT ON COLUMN analysis_forecasts.dividend_state.vhigh_bar IS 'Recorded build parameter: vhigh lower z-bar (default +2.0).';
COMMENT ON COLUMN analysis_forecasts.dividend_state.lookback_period IS 'Recorded build parameter (NOT a PK member): the trailing calendar window the bucket was computed over — ''5y'' = (stat_month - 5 years, stat_month]. Default ''5y''; a rebuild with a different lookback requires --force.';
COMMENT ON COLUMN analysis_forecasts.dividend_state.is_market_hyped IS 'TRUE when ANY of the bucket''s dates falls inside one of the code''s stats.mov_ave_market_hypes episodes (any min_checkin_period).';
