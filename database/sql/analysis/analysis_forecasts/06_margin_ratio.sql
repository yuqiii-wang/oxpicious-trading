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
--  via forecast_id (1:N — 4 period rows next/5d/20d/60d).
--
--  Threshold columns are RECORDED BUILD PARAMETERS (NOT part of the
--  PK — rebuilding with different values requires --force). The
--  full-5y window gate is identical to the other engines.
-- ============================================================================

CREATE TABLE IF NOT EXISTS analysis_forecasts.margin_ratio_state (
    sec_type        TEXT         NOT NULL,  -- 'etf' | 'stock' (index: no margin data → no rows)
    code            TEXT         NOT NULL,
    stat_month      DATE         NOT NULL,  -- completed month-end; bucket over trailing 5y window (stat_month - 5y, stat_month]
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

    forecast_id      BIGINT      NOT NULL,  -- 1:N link → forecast_results (4 period rows)

    -- motivation cols
    is_market_hyped BOOLEAN      NOT NULL,  -- ANY bucket date inside a mov_ave_market_hypes episode (any check-in period)

    CONSTRAINT pk_margin_ratio_state PRIMARY KEY (code, sec_type, stat_month, ratio_state, is_market_hyped)
) PARTITION BY HASH (code);

SELECT public.create_hash_partitions('analysis_forecasts', 'margin_ratio_state', 16);

CREATE INDEX IF NOT EXISTS idx_margin_ratio_state_forecast_id
    ON analysis_forecasts.margin_ratio_state (forecast_id);

-- ----------------------------------------------------------------------------
--  Comments
-- ----------------------------------------------------------------------------
COMMENT ON TABLE analysis_forecasts.margin_ratio_state IS 'Margin-buy intensity state buckets (motivation): per (code, sec_type, stat_month, ratio_state, is_market_hyped) — the window days whose 融资买入额/成交额 ratio (rz_buy / trading_amount, RONGZI only, etf + stock) sits in the named state of the code''s OWN trailing distribution: z = (ratio - μ)/σ with rolling-1220-row (min 250 non-NULL) moments shifted 1 row; no_buy = rz_buy <= 0 that day. States: vlow z<=-2 / low (-2,-1] / mid (-1,+1] / high (+1,+2] / vhigh z>2. Crowding (contrarian) semantics per the 2026-09 study (docs/margin_ratio_study.md): high/vhigh = bearish (side top), vlow/low/no_buy = mild bullish (side bottom), mid = flat. State cells: no cooldown. Results (forward changes / adaptive reverse_threshold k·σ reversal probabilities) live in analysis_forecasts.forecast_results via forecast_id; mid rows carry side=''flat'' and NULL reverse_prob. Sources: stats.{etf,stock}_liquidity_margin (rz_buy, trading_amount). Populated by python -m analyze.analysis_forecasts.';
COMMENT ON COLUMN analysis_forecasts.margin_ratio_state.sec_type IS 'Security type: etf or stock (index has no own margin data — the fetch LEFT JOIN yields NULL rz_buy and no buckets).';
COMMENT ON COLUMN analysis_forecasts.margin_ratio_state.code IS 'Ticker with exchange suffix (e.g. "510050.SS").';
COMMENT ON COLUMN analysis_forecasts.margin_ratio_state.stat_month IS 'Completed month-end date. The bucket is computed over the trailing 5-year window (stat_month - 5 years, stat_month] of the code''s own trading days.';
COMMENT ON COLUMN analysis_forecasts.margin_ratio_state.ratio_state IS 'Margin-intensity state of the day: no_buy (rz_buy <= 0 with trading_amount > 0 — margin traders absent); on buy days z = (ratio - μ)/σ of the code''s rolling-1220-row (min 250) ratio moments shifted 1 row: vlow z <= -2; low -2 < z <= -1; mid -1 < z <= +1; high +1 < z <= +2; vhigh z > +2. Undefined z (short history) → no bucket.';
COMMENT ON COLUMN analysis_forecasts.margin_ratio_state.side IS 'Reversal side of the bucket''s forecast_results.reverse_prob: top (high/vhigh — the crowding states; reversal = n-day change below -reverse_threshold, the study''s bearish reading), bottom (vlow/low/no_buy — reversal above +reverse_threshold), flat (mid — no directional claim; reverse_prob NULL). Mirrors the mov_* / px_vol side semantics so analysis_signals.gate consumes the table unchanged.';
COMMENT ON COLUMN analysis_forecasts.margin_ratio_state.z_window IS 'Recorded build parameter: rolling window (rows) of the ratio moments μ/σ (default 1220 ≈ 5y of trading rows). Shifted 1 row before use (no look-ahead).';
COMMENT ON COLUMN analysis_forecasts.margin_ratio_state.z_min_periods IS 'Recorded build parameter: minimum non-NULL ratio observations inside z_window for z to be defined (default 250 buy days).';
COMMENT ON COLUMN analysis_forecasts.margin_ratio_state.vlow_bar IS 'Recorded build parameter: vlow upper z-bar (default -2.0).';
COMMENT ON COLUMN analysis_forecasts.margin_ratio_state.low_bar IS 'Recorded build parameter: low upper z-bar (default -1.0).';
COMMENT ON COLUMN analysis_forecasts.margin_ratio_state.high_bar IS 'Recorded build parameter: high lower z-bar (default +1.0).';
COMMENT ON COLUMN analysis_forecasts.margin_ratio_state.vhigh_bar IS 'Recorded build parameter: vhigh lower z-bar (default +2.0).';
COMMENT ON COLUMN analysis_forecasts.margin_ratio_state.forecast_id IS '1:N link to the bucket''s 4 period rows in analysis_forecasts.forecast_results (indexed; allocated by the writer, shared across all 4 periods).';
COMMENT ON COLUMN analysis_forecasts.margin_ratio_state.is_market_hyped IS 'Part of the PK: TRUE when ANY of the bucket''s dates falls inside one of the code''s analysis.mov_ave_market_hypes episodes (any min_checkin_period).';
