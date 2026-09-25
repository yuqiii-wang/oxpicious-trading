-- ============================================================================
--  Table: analysis_forecasts.pe_state
--
--  Tenth MOTIVATION (bucket-defining) table of the forecast analysis:
--  valuation extreme-PERCENTILE buckets over the PE series of
--  analysis.pe (raw PE — index PE from stats.index_valuation.pe,
--  etf/stock PE pre-computed by builds; NULL on no-earnings /
--  invalid-PE days → no bucket there) — the mov_rsi pct convention
--  (2026-09 refactor of the former z-STATE buckets):
--
--    a day is in the bucket when pe sits in the top pct% (side=top
--    bucket) or bottom pct% (side=bottom bucket) of the window's
--    non-NULL pe values — the percentile computed per code over the
--    trailing 10-year window (stat_date - 10y, stat_date] with linear
--    interpolation; pct ∈ {1, 5, 10, 25}. Only EXTREME days form
--    buckets (the central bulk has none — the RSI semantics; the
--    former z ladder's mid/flat state is gone).
--
--  SIDE (pe is LOWER the better — a high PE is an expensive, stretched
--  valuation, the family's defining reading):
--    top-pct% (expensive) days = 'top' (bearish),
--    bottom-pct% (cheap) days = 'bottom' (bullish).
--    The dividend_yield sibling lives in 12_dividend_state.sql — same
--    pct grid, REVERSED side mapping (the two families' "better"
--    direction differs).
--
--  side is MATERIALIZED per row, so analysis_signals.gate and every
--  other consumer read the tables unchanged (dir_ave = -ave_change for
--  top, +ave_change for bottom).
--
--  Universe: index + etf + stock (NULL pe days simply form no bucket).
--
--  Buckets are STREAK-MERGED (the 2026-09 unified pipeline, mov_rsi
--  convention): consecutive grid days qualifying for the same bucket
--  collapse into ONE forecast signal anchored at the run's MID day —
--  the bucket's mean run length is recorded on
--  analysis_forecasts.forecast_identities.streak_signal_days — and the
--  bucket split is by regime_state (stats.market_regimes) exactly like
--  the other
--  engines. Results live in analysis_forecasts.forecast_results via
--  forecast_id (1:N — 4 period rows next/5d/20d/mixed); each
--  signal's trigger excess (the mid day's pe minus the bucket's
--  quantile bar) rides forecast_results.trigger_excess.
--
--  lookback_period is a RECORDED BUILD PARAMETER (NOT part of the PK —
--  rebuilding with a different value requires --force). The full-10y
--  window gate is identical to the other engines.
--  CODE-CLUSTERED, forecast_id-keyed (2026-09 shape): PK (code,
--  forecast_id) on HASH (code) partitions — the code-clustered
--  read/write axis; forecast_id-only joins/searches use the secondary
--  idx_pe_state_forecast_id. code is the ONLY identity column
--  stored here (the partition key); sec_type and stat_date live ONLY
--  in analysis_forecasts.forecast_identities — the search table
--  (see 02_mov_rsi_mov_std.sql).
-- ============================================================================

-- ----------------------------------------------------------------------------
--  Shape migration (2026-09 pct refactor): the z-state bucket shape is
--  retired — the val_state column and the recorded z bars (z_window /
--  z_min_periods / vlow_bar / low_bar / high_bar / vhigh_bar) are gone,
--  the buckets are extreme-percentile cells (side, pct) keyed exactly
--  like mov_rsi. Purge the linked result + registry rows FIRST (the
--  result delete resolves (sec_type, bucket) through the registry),
--  then DROP the table with its 16 hash partitions (CASCADE — the
--  mov_gap retirement precedent, 02_mov_rsi_mov_std.sql). Idempotent:
--  no-ops once the table is gone; the pipeline repopulates.
-- ----------------------------------------------------------------------------

DELETE FROM analysis_forecasts.forecast_results f
USING analysis_forecasts.forecast_identities i
WHERE i.forecast_id = f.forecast_id AND i.bucket = 'pe_state';

DELETE FROM analysis_forecasts.forecast_identities
WHERE bucket = 'pe_state';

DROP TABLE IF EXISTS analysis_forecasts.pe_state CASCADE;

CREATE TABLE IF NOT EXISTS analysis_forecasts.pe_state (
    code            TEXT         NOT NULL,  -- hash partition key + PK lead; sec_type / stat_date live in forecast_identities
    forecast_id     BIGINT       NOT NULL,  -- 1:N link to the bucket's 4 forecast_results period rows; id-only joins/searches use idx_pe_state_forecast_id
    side            TEXT         NOT NULL,  -- pe LOWER the better: top-pct% (expensive) days → 'top' (bearish), bottom-pct% (cheap) days → 'bottom' (bullish)
    pct             INTEGER      NOT NULL,  -- percentile width of the extreme bucket: 1 / 5 / 10 / 25

    -- recorded build parameter (NOT PK): trailing window the bucket was
    -- computed over — '10y' = (stat_date - 10y, stat_date]
    lookback_period TEXT         NOT NULL DEFAULT '10y',

    -- motivation cols
    regime_state    TEXT         NOT NULL,  -- the bucket's market-regime split (stats.market_regimes day label of its bucket days)

    CONSTRAINT ck_pe_state_regime
        CHECK (regime_state IN ('calm', 'hot', 'panic', 'quiet')),

    CONSTRAINT pk_pe_state PRIMARY KEY (code, forecast_id),
    CONSTRAINT chk_pe_state_side CHECK (side IN ('top', 'bottom'))
) PARTITION BY HASH (code);

SELECT public.create_hash_partitions('analysis_forecasts', 'pe_state', 16);

CREATE INDEX IF NOT EXISTS idx_pe_state_forecast_id
    ON analysis_forecasts.pe_state (forecast_id);

-- ----------------------------------------------------------------------------
--  Comments
-- ----------------------------------------------------------------------------
COMMENT ON TABLE analysis_forecasts.pe_state IS 'Valuation extreme-percentile buckets (motivation) over the PE series of analysis.pe: one row per forecast_id — the window days of one security snapshot whose raw pe (index PE from stats.index_valuation.pe, etf/stock PE pre-computed by builds) sits in the top pct% or bottom pct% of the trailing 10-year window''s non-NULL pe values, per the code''s OWN distribution (linearly-interpolated quantile bars — the mov_rsi pct convention; pct ∈ {1, 5, 10, 25}; only extreme days form buckets). SIDE (pe LOWER the better): top-pct% (expensive) days carry side ''top'' (bearish), bottom-pct% (cheap) days ''bottom'' (bullish). The dividend_yield sibling lives in analysis_forecasts.dividend_state (same pct grid, REVERSED side mapping). Streak-merged signals (consecutive qualifying days = ONE mid-anchored signal; mean run length on forecast_identities.streak_signal_days). Keyed by the surrogate forecast_id (hash partition key); the shared identity (sec_type, code, stat_date) + bucket family live in analysis_forecasts.forecast_identities. Results (forward changes) live in analysis_forecasts.forecast_results via forecast_id. Populated by python -m analyze.analysis_forecasts.';
COMMENT ON COLUMN analysis_forecasts.pe_state.forecast_id IS 'Surrogate PK + hash-partition key (1:N link to the bucket''s 4 period rows in analysis_forecasts.forecast_results, allocated by the writer, shared across all 4 periods). The bucket''s identity (sec_type, code, stat_date) + bucket family are registered in analysis_forecasts.forecast_identities under this id.';
COMMENT ON COLUMN analysis_forecasts.pe_state.side IS 'Directional claim of the bucket — pe is LOWER the better (a high PE is an expensive, stretched valuation): the top-pct% (expensive) bucket days = ''top'' (bearish), the bottom-pct% (cheap) days = ''bottom'' (bullish). Mirrors the mov_* side semantics so analysis_signals.gate consumes the table unchanged.';
COMMENT ON COLUMN analysis_forecasts.pe_state.pct IS 'Percentile width of the extreme bucket: 1, 5, 10 or 25 (percent). The threshold is the window''s (linearly-interpolated) percentile of pe over non-NULL values: the top bucket''s bar at q = 1 - pct/100, the bottom bucket''s at q = pct/100.';
COMMENT ON COLUMN analysis_forecasts.pe_state.lookback_period IS 'Recorded build parameter (NOT a PK member): the trailing calendar window the bucket was computed over — ''10y'' = (stat_date - 10 years, stat_date]. Default ''10y''; a rebuild with a different lookback requires --force.';
COMMENT ON COLUMN analysis_forecasts.pe_state.regime_state IS 'The bucket''s market-regime split: the stats.market_regimes day label (calm / hot / panic / quiet) carried by the bucket''s trigger days — every trigger day joins exactly one regime, so each (config, regime) pair is its own bucket. Replaces the retired is_market_hyped boolean (stats.mov_ave_market_hypes episode overlap); hot is the closest successor of the old TRUE split.';

-- ----------------------------------------------------------------------------
--  Data-quality gate: the pe_state vocabularies (shared helpers, see
--  01_forecast_results.sql / 00_partition_utils.sql). NOT VALID first,
--  validated once by the schema-wide sweep below.
-- ----------------------------------------------------------------------------
SELECT public.ensure_check_constraint(
    'analysis_forecasts.pe_state',
    'chk_pe_state_side',
    $chk$side IN ('top', 'bottom')$chk$);
SELECT public.validate_pending_checks('analysis_forecasts');
