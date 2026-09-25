-- ============================================================================
--  Table: analysis_forecasts.regime_weights
--    Per-code SELF-ADAPTIVE market-regime weights — the walk-forward
--    w(code, family, regime; stat_date) fitted on prior SNAPSHOTS'
--    realized regime-split outcomes (annual grid since the 2026-09-22
--    migration). EVIDENCE / DISPLAY tier: the
--    2026-09 study tested score = confidence × weight as the
--    signal_strategies ordering key and it did NOT beat the equal
--    confidence ordering out-of-sample (the regime split itself
--    carries the value — the in-window metric already encodes the
--    regime lift), so the weights are consumed by the forecasts UI
--    and future research, NOT by signal_order
--    (docs/market_regimes_study.md).
--
--  FIT (the study's Phase-C algorithm, verbatim):
--    For the snapshot published at stat_date M (a year-end), fit ONLY
--    on rows emitted at the K prior annual snapshots [M-1-K, M-1]
--    (their realized outcomes are known long before year-end M — a
--    snapshot's forward windows close ~1 month into the next year):
--      raw_r  = n-weighted mean of the regime split's realized blended
--               dir_ave over the K snapshots
--      shrink = N_r / (N_r + SHRINK_N)         (N_r = summed n)
--      what_r = max(0, raw_r) * shrink, normalized to sum 1 across the
--               regimes present (all-zero -> uniform 1/R)
--      w_r    = LAMBDA * what_r + (1 - LAMBDA) / R
--    Constants (recorded per row for audit): K = K_SNAPSHOTS = 5
--    (prior annual snapshots; 12 annual snapshots before the annual
--    migration), SHRINK_N = 10, LAMBDA = 0.7, R = 4
--    (calm/hot/panic/quiet).
--
--  REALIZED definition (identical to the study): a row emitted at
--  snapshot m realizes the blended sign-aligned dir_ave (5d .65 /
--  next .25 / 20d .10 — MIXED_HORIZON_WEIGHTS) of its OWN bucket's
--  qualifying window days, under the bars frozen at m
--  (live-strategy semantics).
--
--  The (family, code) rows present are whatever the fit window held —
--  missing regimes get NO row (consumers' LEFT JOIN yields NULL ->
--  the uniform fallback 1/R, matching the study's scoring).
--
--  POPULATION: the analysis_forecasts run's regime-weights step
--    (analyze.analysis_forecasts.regime_weights), executed after the
--    family tables write — SQL reads the RAW evidence rows only (one
--    parallel plain SELECT over the pending windows' union; no
--    aggregation, no rounding in SQL), the fit computes in Python
--    (vectorized, 2026-09-25) and the weight rows COPY-load.
-- ============================================================================

DROP TABLE IF EXISTS analysis_forecasts.regime_weights CASCADE;

CREATE TABLE analysis_forecasts.regime_weights (
    stat_date      DATE         NOT NULL,  -- the annual snapshot date the weights order
    sec_type        TEXT         NOT NULL,  -- 'etf' | 'index' | 'stock'
    code            TEXT         NOT NULL,
    family          TEXT         NOT NULL,  -- forecast family vocab: 'mov_rsi' | 'mov_std' | 'margin_ratio_state' | ...
    regime          TEXT         NOT NULL,  -- 'calm' | 'hot' | 'panic' | 'quiet'

    weight          NUMERIC(8,6) NOT NULL,  -- w_r in [0, 1]
    evidence        JSONB,                  -- fit diagnostics: raw_r / shrink / what_r / N_r per regime

    -- recorded fit constants (the study's calibration)
    k_snapshots     SMALLINT     NOT NULL DEFAULT 5,   -- K prior annual snapshots
    shrink_n        NUMERIC(8,2) NOT NULL DEFAULT 10,
    lambda          NUMERIC(4,2) NOT NULL DEFAULT 0.7,

    CONSTRAINT pk_regime_weights PRIMARY KEY (stat_date, sec_type, code, family, regime),
    CONSTRAINT ck_regime_weights_family CHECK (
        family IN ('mov_rsi', 'mov_std', 'margin_ratio_state',
                   'mov_pairs', 'mov_pairs_ema', 'high_low_streaks',
                   'pe_state', 'dividend_state')),
    CONSTRAINT ck_regime_weights_regime CHECK (regime IN ('calm', 'hot', 'panic', 'quiet')),
    CONSTRAINT ck_regime_weights_weight CHECK (weight >= 0 AND weight <= 1)
) PARTITION BY HASH (code);

SELECT public.create_hash_partitions('analysis_forecasts', 'regime_weights', 8);

CREATE INDEX IF NOT EXISTS idx_regime_weights_lookup
    ON analysis_forecasts.regime_weights (sec_type, stat_date, family);

COMMENT ON TABLE  analysis_forecasts.regime_weights IS 'Per-code self-adaptive market-regime weights: one row per (stat_date, sec_type, code, family, regime) — the walk-forward w(code, family, regime) fitted ONLY on prior snapshots'' realized outcomes (the K=5 prior annual snapshots [M-1-K, M-1]). EVIDENCE / DISPLAY tier: the 2026-09 study (docs/market_regimes_study.md) tested score = confidence × weight as the signal_strategies ordering key against the equal confidence ordering and it did NOT win out-of-sample (pooled top-1% 1.50% equal vs 1.36% weighted, monthly win rate 23/55) — the regime split itself carries the value — so the weights feed the forecasts UI (which regimes pay for this code) and future research, not signal_order. Fit: raw = n-weighted mean realized blended dir_ave per regime; shrink = N/(N+10); what = normalized max(0, raw)*shrink; w = 0.7*what + 0.25/4 blend toward uniform. Missing (family, code, regime) rows = no fit evidence -> consumers fall back to the uniform 1/4. Populated by the analysis_forecasts regime-weights step after the family tables write.';
COMMENT ON COLUMN analysis_forecasts.regime_weights.stat_date IS 'The ANNUAL snapshot date (year-end) the weights ORDER — the snapshot whose emitted forecast rows (and their signal_strategies) are scored. Fitted only on snapshots whose forward windows are fully realized (the K prior year-ends).';
COMMENT ON COLUMN analysis_forecasts.regime_weights.family IS 'Forecast family vocab (the forecast_identities bucket values) the weights apply to — weights are fitted per family (the mov_rsi evidence does not transfer to mov_std).';
COMMENT ON COLUMN analysis_forecasts.regime_weights.regime IS 'The regime the weight multiplies (calm / hot / panic / quiet — stats.market_regimes).';
COMMENT ON COLUMN analysis_forecasts.regime_weights.weight IS 'w_r ∈ [0,1]: LAMBDA*what_r + (1-LAMBDA)/4 with what_r the shrunk, sign-clipped, normalized realized lift of the regime''s splits over the fit window. Per (family, code) the present rows'' weights sum to ~1 (missing-regime fallback is the uniform 1/4).';
COMMENT ON COLUMN analysis_forecasts.regime_weights.evidence IS 'Fit diagnostics JSON: {raw_r, shrink, what, N} per regime of the (family, code) group — the audit trail behind the weight.';
