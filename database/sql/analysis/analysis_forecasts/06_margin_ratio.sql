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
--            SHIFTED 1 row (no look-ahead — px_vol convention).
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
--  Buckets are STATE cells (every qualifying day joins — no cooldown,
--  like px_vol_state), split by PK member is_market_hyped exactly like
--  the other engines. side semantics mirror the mov_* / px_vol tables
--  so analysis_signals.gate consumes them unchanged: high/vhigh =
--  'top' (crowding top — reverse_prob = P(change < -threshold), the
--  bearish reading the study supports), no_buy/vlow/low = 'bottom'
--  (reverse = change > +threshold), mid = 'flat' with NULL
--  reverse_prob. Results live in analysis_forecasts.forecast_results
--  via forecast_id (1:N — 5 period rows next/5d/20d/60d/mixed).
--
--  Threshold columns are RECORDED BUILD PARAMETERS (NOT part of the
--  PK — rebuilding with different values requires --force). The
--  full-5y window gate is identical to the other engines.
--  CODE-CLUSTERED, forecast_id-keyed (2026-09): PK (code, forecast_id)
--  on HASH (code) partitions — the code-clustered read/write axis (a
--  per-security read prunes to ONE partition and walks the code-leading
--  PK; forecast_id-only joins/searches use the secondary
--  idx_margin_ratio_state_forecast_id). code is the ONLY identity column stored
--  here (the partition key); sec_type and stat_month live ONLY in
--  analysis_forecasts.forecast_identities — the search table
--  (see 02_mov_rsi_mov_std.sql).
-- ============================================================================

CREATE TABLE IF NOT EXISTS analysis_forecasts.margin_ratio_state (
    code            TEXT         NOT NULL,  -- hash partition key + PK lead; sec_type / stat_month live in forecast_identities
    forecast_id     BIGINT      NOT NULL,  -- 1:N link to the bucket's 5 forecast_results period rows; id-only joins/searches use idx_margin_ratio_state_forecast_id
    ratio_state     TEXT         NOT NULL,  -- 'no_buy' | 'vlow' | 'low' | 'mid' | 'high' | 'vhigh' (z bars of rz_buy/trading_amount)
    side            TEXT         NOT NULL,  -- 'top' (high/vhigh crowding) | 'bottom' (vlow/low/no_buy) | 'flat' (mid) — reversal direction of reverse_prob

    -- Recorded build parameters (NOT PK — recorded for provenance; a
    -- rebuild with different values requires --force).
    z_window        INTEGER      NOT NULL DEFAULT 1220,  -- rolling μ/σ window of ratio (rows ≈ 5y of trading days)
    z_min_periods   INTEGER      NOT NULL DEFAULT 250,   -- min non-NULL ratio observations in the window
    vlow_bar        NUMERIC(4,2) NOT NULL DEFAULT -2.00, -- vlow upper z-bar (z <= vlow_bar)
    low_bar         NUMERIC(4,2) NOT NULL DEFAULT -1.00, -- low upper z-bar (vlow_bar < z <= low_bar)
    high_bar        NUMERIC(4,2) NOT NULL DEFAULT 1.00,  -- high lower z-bar (high_bar < z <= vhigh_bar)
    vhigh_bar       NUMERIC(4,2) NOT NULL DEFAULT 2.00,  -- vhigh lower z-bar (z > vhigh_bar)
    lookback_period TEXT         NOT NULL DEFAULT '5y',  -- trailing window the bucket was computed over ('5y' = stat_month - 5y .. stat_month)

    -- motivation cols
    is_market_hyped BOOLEAN      NOT NULL,  -- ANY bucket date inside a mov_ave_market_hypes episode (any check-in period)

    CONSTRAINT pk_margin_ratio_state PRIMARY KEY (code, forecast_id)
) PARTITION BY HASH (code);

SELECT public.create_hash_partitions('analysis_forecasts', 'margin_ratio_state', 16);

-- ----------------------------------------------------------------------------
--  Migration (2026-09): forecast_id-keyed rebuild — the composite
--  (code, sec_type, stat_month, ...) PK is replaced by the surrogate
--  forecast_id (PK + hash-partition key); the shared identity lives
--  ONLY in forecast_identities. Legacy-shape tables (they still carry
--  the code column) are rebuilt by swap — rows are carried over via
--  forecast_id and the old secondary forecast_id index dissolves into
--  the new PK. See 02_mov_rsi_mov_std.sql for the full rationale.
-- ----------------------------------------------------------------------------
DO $$
DECLARE
    r          int;
    v_partkey  text;
    v_pkdef    text;
    v_has_code bool;
BEGIN
    SELECT pg_get_partkeydef(c.oid),
           COALESCE((SELECT pg_get_constraintdef(p.oid)
                     FROM pg_constraint p
                     WHERE p.conrelid = c.oid AND p.contype = 'p'), '')
    INTO v_partkey, v_pkdef
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'analysis_forecasts' AND c.relname = 'margin_ratio_state';
    SELECT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_schema = 'analysis_forecasts'
                     AND table_name   = 'margin_ratio_state'
                     AND column_name  = 'code')
    INTO v_has_code;
    IF v_partkey IS NULL
       OR (v_partkey = 'HASH (code)'
           AND v_pkdef = 'PRIMARY KEY (code, forecast_id)') THEN
        -- fresh install (created above in the target shape) or already
        -- migrated
        RETURN;
    END IF;
    ALTER TABLE analysis_forecasts.margin_ratio_state RENAME TO margin_ratio_state_pk_rebuild;
    ALTER TABLE analysis_forecasts.margin_ratio_state_pk_rebuild
        DROP CONSTRAINT IF EXISTS pk_margin_ratio_state;
    CREATE TABLE analysis_forecasts.margin_ratio_state_new (
            code            TEXT         NOT NULL,  -- hash partition key + PK lead; sec_type / stat_month live in forecast_identities
            forecast_id     BIGINT      NOT NULL,  -- 1:N link to the bucket's 5 forecast_results period rows; id-only joins/searches use idx_margin_ratio_state_forecast_id
            ratio_state     TEXT         NOT NULL,
            side            TEXT         NOT NULL,
            z_window        INTEGER      NOT NULL DEFAULT 1220,
            z_min_periods   INTEGER      NOT NULL DEFAULT 250,
            vlow_bar        NUMERIC(4,2) NOT NULL DEFAULT -2.00,
            low_bar         NUMERIC(4,2) NOT NULL DEFAULT -1.00,
            high_bar        NUMERIC(4,2) NOT NULL DEFAULT 1.00,
            vhigh_bar       NUMERIC(4,2) NOT NULL DEFAULT 2.00,
            lookback_period TEXT         NOT NULL DEFAULT '5y',
            is_market_hyped BOOLEAN      NOT NULL,
        CONSTRAINT pk_margin_ratio_state PRIMARY KEY (code, forecast_id)
    ) PARTITION BY HASH (code);
    PERFORM public.create_hash_partitions('analysis_forecasts',
                                          'margin_ratio_state_new', 16);
    ALTER TABLE analysis_forecasts.margin_ratio_state_pk_rebuild
        ADD COLUMN IF NOT EXISTS lookback_period TEXT NOT NULL DEFAULT '5y';
    IF v_has_code THEN
        INSERT INTO analysis_forecasts.margin_ratio_state_new
               (code, forecast_id, ratio_state, side, z_window, z_min_periods, vlow_bar, low_bar, high_bar, vhigh_bar, lookback_period, is_market_hyped)
        SELECT  code, forecast_id, ratio_state, side, z_window, z_min_periods, vlow_bar, low_bar, high_bar, vhigh_bar, lookback_period, is_market_hyped
        FROM    analysis_forecasts.margin_ratio_state_pk_rebuild;
    ELSE
        -- intermediate forecast_id-keyed shape (no code column): code
        -- comes from the identities registry (1 row per forecast_id)
        INSERT INTO analysis_forecasts.margin_ratio_state_new
               (code, forecast_id, ratio_state, side, z_window, z_min_periods, vlow_bar, low_bar, high_bar, vhigh_bar, lookback_period, is_market_hyped)
        SELECT  i.code, m.forecast_id, m.ratio_state, m.side, m.z_window, m.z_min_periods, m.vlow_bar, m.low_bar, m.high_bar, m.vhigh_bar, m.lookback_period, m.is_market_hyped
        FROM    analysis_forecasts.margin_ratio_state_pk_rebuild m
        JOIN    analysis_forecasts.forecast_identities i
          ON    i.forecast_id = m.forecast_id;
    END IF;
    DROP TABLE analysis_forecasts.margin_ratio_state_pk_rebuild;
    ALTER TABLE analysis_forecasts.margin_ratio_state_new RENAME TO margin_ratio_state;
    FOR r IN 0..15 LOOP
        EXECUTE format(
            'ALTER TABLE analysis_forecasts.margin_ratio_state_new_p%s '
            'RENAME TO margin_ratio_state_p%s',
            lpad(r::text, 2, '0'), lpad(r::text, 2, '0'));
    END LOOP;
END $$;

CREATE INDEX IF NOT EXISTS idx_margin_ratio_state_forecast_id
    ON analysis_forecasts.margin_ratio_state (forecast_id);

-- ----------------------------------------------------------------------------
--  Comments
-- ----------------------------------------------------------------------------
COMMENT ON TABLE analysis_forecasts.margin_ratio_state IS 'Margin-buy intensity state buckets (motivation): one row per forecast_id — the window days of one security-month whose 融资买入额/成交额 ratio (rz_buy / trading_amount, RONGZI only, etf + stock) sits in the named state of the code''s OWN trailing distribution: z = (ratio - μ)/σ with rolling-1220-row (min 250 non-NULL) moments shifted 1 row; no_buy = rz_buy <= 0 that day. States: vlow z<=-2 / low (-2,-1] / mid (-1,+1] / high (+1,+2] / vhigh z>2. Crowding (contrarian) semantics per the 2026-09 study (docs/margin_ratio_study.md): high/vhigh = bearish (side top), vlow/low/no_buy = mild bullish (side bottom), mid = flat. State cells: no cooldown. Keyed by the surrogate forecast_id (hash partition key); the shared identity (sec_type, code, stat_month) + bucket family live in analysis_forecasts.forecast_identities. Results (forward changes / FIXED 1% threshold — period-end n-day close vs ±1% — reversal probabilities) live in analysis_forecasts.forecast_results via forecast_id; mid rows carry side=''flat'' and NULL reverse_prob. Sources: stats.{etf,stock}_liquidity_margin (rz_buy, trading_amount). Populated by python -m analyze.analysis_forecasts.';
COMMENT ON COLUMN analysis_forecasts.margin_ratio_state.forecast_id IS 'Surrogate PK + hash-partition key (1:N link to the bucket''s 5 period rows in analysis_forecasts.forecast_results, allocated by the writer, shared across all 5 periods). The bucket''s identity (sec_type, code, stat_month) + bucket family are registered in analysis_forecasts.forecast_identities under this id.';
COMMENT ON COLUMN analysis_forecasts.margin_ratio_state.ratio_state IS 'Margin-intensity state of the day: no_buy (rz_buy <= 0 with trading_amount > 0 — margin traders absent); on buy days z = (ratio - μ)/σ of the code''s rolling-1220-row (min 250) ratio moments shifted 1 row: vlow z <= -2; low -2 < z <= -1; mid -1 < z <= +1; high +1 < z <= +2; vhigh z > +2. Undefined z (short history) → no bucket.';
COMMENT ON COLUMN analysis_forecasts.margin_ratio_state.side IS 'Reversal side of the bucket''s forecast_results.reverse_prob: top (high/vhigh — the crowding states; reversal = n-day change below -threshold, the study''s bearish reading), bottom (vlow/low/no_buy — reversal above +threshold), flat (mid — no directional claim; reverse_prob NULL). Mirrors the mov_* / px_vol side semantics so analysis_signals.gate consumes the table unchanged.';
COMMENT ON COLUMN analysis_forecasts.margin_ratio_state.z_window IS 'Recorded build parameter: rolling window (rows) of the ratio moments μ/σ (default 1220 ≈ 5y of trading rows). Shifted 1 row before use (no look-ahead).';
COMMENT ON COLUMN analysis_forecasts.margin_ratio_state.z_min_periods IS 'Recorded build parameter: minimum non-NULL ratio observations inside z_window for z to be defined (default 250 buy days).';
COMMENT ON COLUMN analysis_forecasts.margin_ratio_state.vlow_bar IS 'Recorded build parameter: vlow upper z-bar (default -2.0).';
COMMENT ON COLUMN analysis_forecasts.margin_ratio_state.low_bar IS 'Recorded build parameter: low upper z-bar (default -1.0).';
COMMENT ON COLUMN analysis_forecasts.margin_ratio_state.high_bar IS 'Recorded build parameter: high lower z-bar (default +1.0).';
COMMENT ON COLUMN analysis_forecasts.margin_ratio_state.vhigh_bar IS 'Recorded build parameter: vhigh lower z-bar (default +2.0).';
COMMENT ON COLUMN analysis_forecasts.margin_ratio_state.lookback_period IS 'Recorded build parameter (NOT a PK member): the trailing calendar window the bucket was computed over — ''5y'' = (stat_month - 5 years, stat_month]. Default ''5y''; a rebuild with a different lookback requires --force.';
COMMENT ON COLUMN analysis_forecasts.margin_ratio_state.is_market_hyped IS 'TRUE when ANY of the bucket''s dates falls inside one of the code''s stats.mov_ave_market_hypes episodes (any min_checkin_period).';
