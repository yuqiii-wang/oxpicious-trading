-- ============================================================================
--  Table: analysis_forecasts.margin_ratio_state
--
--  Fifth MOTIVATION (bucket-defining) table of the forecast analysis:
--  margin-buy intensity STATE buckets — the daily 融资买入额/成交额 ratio
--  (rz_buy / trading_amount) standardized by the code's OWN trailing
--  moments (2026-09 study: temp_scripts/study_margin_ratio_forecast.py,
--  docs/margin_ratio_study.md). RONGZI only; etf + stock (index has no
--  own margin data → no buckets).
--
--  Per (code, date) with trading_amount > 0:
--
--    ratio — rz_buy / trading_amount on margin-buy days (rz_buy > 0);
--            NULL otherwise
--    z     — (ratio - μ) / σ with μ/σ = the code's rolling
--            z_window-row (default 1220 = 5y of trading rows),
--            min_periods z_min_periods (default 250) moments of ratio,
--            SHIFTED 1 row (no look-ahead).
--            Undefined where ratio is NULL or the history is short.
--
--    ratio_state:
--      no_buy : rz_buy <= 0 that day (margin traders absent — the
--               universe is margin-active codes, so this is "inactive
--               today", not "never trades margin")
--      vlow   : z <= vlow_bar   (default -2.0)
--      low    : vlow_bar < z <= low_bar  (-2.0 / -1.0)
--      mid    : low_bar  < z <= high_bar (central bulk — no claim)
--      high   : high_bar < z <= vhigh_bar (1.0 / 2.0)
--      vhigh  : z > vhigh_bar   (default +2.0)
--
--  Study verdict the buckets encode (docs/margin_ratio_study.md): the
--  ratio is a CROWDING (contrarian) indicator — high states carry
--  NEGATIVE 5d/20d forward-change lift (monthly cross-sectional
--  trend5 IC -0.040, 82% of months negative) and HIGHER forward
--  realized volatility (vol5 IC +0.054, 90% of months positive);
--  low / no_buy states show mild positive drift at lower volatility.
--
--  Buckets are STATE cells (every qualifying day joins — no cooldown),
--  split by regime_state (stats.market_regimes)
--  exactly like
--  the other engines. side semantics mirror the mov_* tables
--  so analysis_signals.gate consumes them unchanged: high/vhigh =
--  'top' (crowding top — the bearish reading the study supports),
--  no_buy/vlow/low = 'bottom', mid = 'flat' (no directional claim).
--  Results live in analysis_forecasts.forecast_results
--  via forecast_id (1:N — 4 period rows next/5d/20d/mixed).
--
--  Threshold columns are RECORDED BUILD PARAMETERS (NOT part of the
--  PK — rebuilding with different values requires --force). The
--  full-10y window gate is identical to the other engines.
--  CODE-CLUSTERED, forecast_id-keyed (2026-09): PK (code, forecast_id)
--  on HASH (code) partitions — the code-clustered read/write axis (a
--  per-security read prunes to ONE partition and walks the code-leading
--  PK; forecast_id-only joins/searches use the secondary
--  idx_margin_ratio_state_forecast_id). code is the ONLY identity column stored
--  here (the partition key); sec_type and stat_date live ONLY in
--  analysis_forecasts.forecast_identities — the search table
--  (see 02_mov_rsi_mov_std.sql).
-- ============================================================================

CREATE TABLE IF NOT EXISTS analysis_forecasts.margin_ratio_state (
    code            TEXT         NOT NULL,  -- hash partition key + PK lead; sec_type / stat_date live in forecast_identities
    forecast_id     BIGINT      NOT NULL,  -- 1:N link to the bucket's 4 forecast_results period rows; id-only joins/searches use idx_margin_ratio_state_forecast_id
    ratio_state     TEXT         NOT NULL,  -- 'no_buy' | 'vlow' | 'low' | 'mid' | 'high' | 'vhigh' (z bars of rz_buy/trading_amount)
    side            TEXT         NOT NULL,  -- 'top' (high/vhigh crowding) | 'bottom' (vlow/low/no_buy) | 'flat' (mid) — the bucket's directional claim

    -- Recorded build parameters (NOT PK — recorded for provenance; a
    -- rebuild with different values requires --force).
    z_window        INTEGER      NOT NULL DEFAULT 1220,  -- rolling μ/σ window of ratio (rows ≈ 5y of trading days)
    z_min_periods   INTEGER      NOT NULL DEFAULT 250,   -- min non-NULL ratio observations in the window
    vlow_bar        NUMERIC(4,2) NOT NULL DEFAULT -2.00, -- vlow upper z-bar (z <= vlow_bar)
    low_bar         NUMERIC(4,2) NOT NULL DEFAULT -1.00, -- low upper z-bar (vlow_bar < z <= low_bar)
    high_bar        NUMERIC(4,2) NOT NULL DEFAULT 1.00,  -- high lower z-bar (high_bar < z <= vhigh_bar)
    vhigh_bar       NUMERIC(4,2) NOT NULL DEFAULT 2.00,  -- vhigh lower z-bar (z > vhigh_bar)
    lookback_period TEXT         NOT NULL DEFAULT '10y',  -- trailing window the bucket was computed over ('10y' = stat_date - 10y .. stat_date)

    -- motivation cols
    regime_state    TEXT         NOT NULL,  -- the bucket's market-regime split (stats.market_regimes day label of its bucket days)

    CONSTRAINT ck_margin_ratio_state_regime
        CHECK (regime_state IN ('calm', 'hot', 'panic', 'quiet')),

    CONSTRAINT pk_margin_ratio_state PRIMARY KEY (code, forecast_id)
) PARTITION BY HASH (code);

SELECT public.create_hash_partitions('analysis_forecasts', 'margin_ratio_state', 16);

CREATE INDEX IF NOT EXISTS idx_margin_ratio_state_forecast_id
    ON analysis_forecasts.margin_ratio_state (forecast_id);

-- ----------------------------------------------------------------------------
--  Comments
-- ----------------------------------------------------------------------------
COMMENT ON TABLE analysis_forecasts.margin_ratio_state IS 'Margin-buy intensity state buckets (motivation): one row per forecast_id — the window days of one security-snapshot whose 融资买入额/成交额 ratio (rz_buy / trading_amount, RONGZI only, etf + stock) sits in the named state of the code''s OWN trailing distribution: z = (ratio - μ)/σ with rolling-1220-row (min 250 non-NULL) moments shifted 1 row; no_buy = rz_buy <= 0 that day. States: vlow z<=-2 / low (-2,-1] / mid (-1,+1] / high (+1,+2] / vhigh z>2. Crowding (contrarian) semantics per the 2026-09 study (docs/margin_ratio_study.md): high/vhigh = bearish (side top), vlow/low/no_buy = mild bullish (side bottom), mid = flat. State cells: no cooldown. Keyed by the surrogate forecast_id (hash partition key); the shared identity (sec_type, code, stat_date) + bucket family live in analysis_forecasts.forecast_identities. Results (forward changes) live in analysis_forecasts.forecast_results via forecast_id. Sources: stats.{etf,stock}_liquidity_margin (rz_buy, trading_amount). Populated by python -m analyze.analysis_forecasts.';
COMMENT ON COLUMN analysis_forecasts.margin_ratio_state.forecast_id IS 'Surrogate PK + hash-partition key (1:N link to the bucket''s 4 period rows in analysis_forecasts.forecast_results, allocated by the writer, shared across all 4 periods). The bucket''s identity (sec_type, code, stat_date) + bucket family are registered in analysis_forecasts.forecast_identities under this id.';
COMMENT ON COLUMN analysis_forecasts.margin_ratio_state.ratio_state IS 'Margin-intensity state of the day: no_buy (rz_buy <= 0 with trading_amount > 0 — margin traders absent); on buy days z = (ratio - μ)/σ of the code''s rolling-1220-row (min 250) ratio moments shifted 1 row: vlow z <= -2; low -2 < z <= -1; mid -1 < z <= +1; high +1 < z <= +2; vhigh z > +2. Undefined z (short history) → no bucket.';
COMMENT ON COLUMN analysis_forecasts.margin_ratio_state.side IS 'Directional claim of the bucket: top (high/vhigh — the crowding states; the study''s bearish reading), bottom (vlow/low/no_buy — mild bullish), flat (mid — no directional claim). Mirrors the mov_* side semantics so analysis_signals.gate consumes the table unchanged.';
COMMENT ON COLUMN analysis_forecasts.margin_ratio_state.z_window IS 'Recorded build parameter: rolling window (rows) of the ratio moments μ/σ (default 1220 ≈ 5y of trading rows). Shifted 1 row before use (no look-ahead).';
COMMENT ON COLUMN analysis_forecasts.margin_ratio_state.z_min_periods IS 'Recorded build parameter: minimum non-NULL ratio observations inside z_window for z to be defined (default 250 buy days).';
COMMENT ON COLUMN analysis_forecasts.margin_ratio_state.vlow_bar IS 'Recorded build parameter: vlow upper z-bar (default -2.0).';
COMMENT ON COLUMN analysis_forecasts.margin_ratio_state.low_bar IS 'Recorded build parameter: low upper z-bar (default -1.0).';
COMMENT ON COLUMN analysis_forecasts.margin_ratio_state.high_bar IS 'Recorded build parameter: high lower z-bar (default +1.0).';
COMMENT ON COLUMN analysis_forecasts.margin_ratio_state.vhigh_bar IS 'Recorded build parameter: vhigh lower z-bar (default +2.0).';
COMMENT ON COLUMN analysis_forecasts.margin_ratio_state.lookback_period IS 'Recorded build parameter (NOT a PK member): the trailing calendar window the bucket was computed over — ''10y'' = (stat_date - 10 years, stat_date]. Default ''10y''; a rebuild with a different lookback requires --force.';
COMMENT ON COLUMN analysis_forecasts.margin_ratio_state.regime_state IS 'The bucket''s market-regime split: the stats.market_regimes day label (calm / hot / panic / quiet) carried by the bucket''s trigger days — every trigger day joins exactly one regime, so each (config, regime) pair is its own bucket. Replaces the retired is_market_hyped boolean (stats.mov_ave_market_hypes episode overlap); hot is the closest successor of the old TRUE split.';

-- ----------------------------------------------------------------------------
--  Data-quality gate: the margin_ratio_state vocabularies (shared helpers, see
--  01_forecast_results.sql / 00_partition_utils.sql). NOT VALID first,
--  validated once by the schema-wide sweep below.
-- ----------------------------------------------------------------------------
SELECT public.ensure_check_constraint(
    'analysis_forecasts.margin_ratio_state',
    'chk_margin_ratio_state_side',
    $chk$side IN ('top', 'bottom', 'flat')$chk$);
SELECT public.ensure_check_constraint(
    'analysis_forecasts.margin_ratio_state',
    'chk_margin_ratio_state_ratio_state',
    $chk$ratio_state IN ('no_buy', 'vlow', 'low', 'mid', 'high', 'vhigh')$chk$);
SELECT public.validate_pending_checks('analysis_forecasts');
