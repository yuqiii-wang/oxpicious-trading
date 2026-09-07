-- ============================================================================
--  Table: analysis.mov_ave_price_vs_amt
--
--  Per-(code, date) Price × Trading-Amount STATE registry of the
--  mov_ave_spread analysis (price_vs_amt.py internal step): every day
--  whose state inputs are valid joins EXACTLY ONE of the 15
--  price-speed × amount-state categories. This is the DATE-LEVEL source
--  of truth for the px_vol family — analysis_forecasts.px_vol_state
--  stores bucket AGGREGATES only (no member dates), so the forecast /
--  signal engines and the MA-Spread UI shading all AUDIT against this
--  table instead of re-deriving the categories.
--
--  A (code, date) gets a row when BOTH legs are computable with
--  information available at that day (every rolling stat is shifted
--  1 row → no look-ahead; identical definitions to
--  analysis_forecasts.px_vol_state / fetch.add_px_vol_features):
--
--    px_speed — the day's 1-row fractional price change ret_1d,
--               standardized by the code's OWN trailing σ:
--                 t = ret_1d / σ_ret(code, 255 rows ending t-1)
--               (σ_ret = rolling 255-row sample std ddof=1 of ret_1d,
--               min_periods 60, shifted 1 row):
--                 sharp_up : t >  k_sharp   (default 2.00)
--                 slow_up  : k_slow_up < t <= k_sharp  (1.26 / 2.00)
--                 flat     : -k_slow_dn <= t <= k_slow_up  (-1.29 / 1.26)
--                 slow_dn  : -k_sharp <= t < -k_slow_dn (-2.00 / -1.29)
--                 sharp_dn : t < -k_sharp
--               No row when σ_ret is NaN or below sigma_floor (0.005 —
--               bond-like indices are excluded).
--
--    vol_state — the day's 量比 z-scored by the code's own trailing
--               moments:
--                 amt_ratio = trading_amount[t] / mean(trading_amount,
--                           t-5..t-1)   (classic, excludes today)
--                 z = (amt_ratio - μ) / σ   with μ/σ = rolling 255-row
--                 (min_periods 60, ddof=1) moments of amt_ratio, shifted
--                 1 row:
--                 heavy  : z >  z_heavy   (default 2.00)
--                 normal : z_shrink <= z <= z_heavy  (-0.92 / 2.00)
--                 shrink : z <  z_shrink  (default -0.92)
--               NULL trading_amount (or missing base window) → no row.
--
--  side mirrors px_vol_state: top (up speeds) / bottom (down speeds) /
--  flat — the reversal direction of the family's reverse_prob. The
--  state values (px_t / px_z / ret_1d / px_sigma / amt_ratio) are
--  recorded so consumers can compute means (the forecast buckets'
--  config mean_t / mean_z) without re-deriving the features.
--
--  The threshold columns are RECORDED BUILD PARAMETERS (like
--  px_vol_state's): NOT part of the PK — rebuilding with different
--  values requires --force. The forecast / signal engines VERIFY the
--  recorded set against their own constants before consuming.
--
--  REBUILD SEMANTICS (market_hypes precedent): rows are rebuilt
--  WHOLESALE per sec_type (or per --code) on every mov_ave_spread run —
--  ETF adj_close back-adjustments (dividends) rewrite the whole price
--  history, so per-date incremental upserts cannot be trusted.
-- ============================================================================

CREATE TABLE IF NOT EXISTS analysis.mov_ave_price_vs_amt (
    sec_type        TEXT         NOT NULL,  -- 'etf' | 'index' | 'stock'
    code            TEXT         NOT NULL,
    date            DATE         NOT NULL,  -- the state day

    px_speed        TEXT         NOT NULL,  -- 'sharp_up' | 'slow_up' | 'flat' | 'slow_dn' | 'sharp_dn'
    vol_state       TEXT         NOT NULL,  -- 'heavy' | 'normal' | 'shrink'
    side            TEXT         NOT NULL,  -- 'top' (up speeds) | 'bottom' (down speeds) | 'flat'

    -- Recorded state values (the category's evidence, no look-ahead).
    px_t            DOUBLE PRECISION NOT NULL,  -- ret_1d / px_sigma (σ-standardized price speed)
    px_z            DOUBLE PRECISION NOT NULL,  -- z-scored 量比 (trading-amount state)
    ret_1d          DOUBLE PRECISION NOT NULL,  -- 1-row fractional price change
    px_sigma        DOUBLE PRECISION NOT NULL,  -- rolling-255 σ_ret (ddof=1, min 60, shifted 1 row)
    amt_ratio       DOUBLE PRECISION NOT NULL,  -- trading_amount / mean(trading_amount[t-5..t-1])

    -- Recorded build parameters (NOT PK — provenance; a rebuild with
    -- different values requires --force; consumers verify).
    sigma_window    INTEGER      NOT NULL DEFAULT 255,
    lb_window       INTEGER      NOT NULL DEFAULT 5,
    k_slow_up       NUMERIC(4,2) NOT NULL DEFAULT 1.26,
    k_slow_dn       NUMERIC(4,2) NOT NULL DEFAULT 1.29,
    k_sharp         NUMERIC(4,2) NOT NULL DEFAULT 2.00,
    z_heavy         NUMERIC(4,2) NOT NULL DEFAULT 2.00,
    z_shrink        NUMERIC(4,2) NOT NULL DEFAULT -0.92,
    sigma_floor     NUMERIC(6,4) NOT NULL DEFAULT 0.005,

    CONSTRAINT pk_mov_ave_price_vs_amt PRIMARY KEY (sec_type, code, date)
) PARTITION BY HASH (code);

SELECT public.create_hash_partitions('analysis', 'mov_ave_price_vs_amt', 16);

-- ----------------------------------------------------------------------------
--  Comments
-- ----------------------------------------------------------------------------
COMMENT ON TABLE analysis.mov_ave_price_vs_amt IS 'Per-(code, date) Price × Trading-Amount state registry (mov_ave_spread internal step): every day with valid inputs joins exactly ONE of the 15 px_speed × vol_state categories — σ-standardized 1-day price change (t = ret_1d / rolling-255 σ_ret ddof=1, min 60, shifted 1 row; σ floor 0.005 excludes bond-like codes) × z-scored 量比 (trading_amount vs its own 5-row trailing mean; z vs rolling-255 moments, shifted 1 row). The DATE-LEVEL source of truth of the px_vol family: analysis_forecasts.px_vol_state (bucket aggregates) and the MA-Spread UI shading audit against this table. Same price convention as the parent analysis (ETF = COALESCE(adj_close, close)). Populated by python -m analyze.mov_ave_spread (price_vs_amt.py step, wholesale per sec_type).';
COMMENT ON COLUMN analysis.mov_ave_price_vs_amt.sec_type IS 'Security type: etf (ETF), index (CSI-style index), or stock (individual equity).';
COMMENT ON COLUMN analysis.mov_ave_price_vs_amt.code IS 'Ticker. ETFs use exchange suffix (e.g. "510050.SS"); indices use bare code (e.g. "000300").';
COMMENT ON COLUMN analysis.mov_ave_price_vs_amt.date IS 'The state day. States are backward-looking (all rolling stats shifted 1 row), but rows are rebuilt wholesale per sec_type because ETF adj_close back-adjustments rewrite price history.';
COMMENT ON COLUMN analysis.mov_ave_price_vs_amt.px_speed IS 'Price-speed state: t = ret_1d / σ_ret(code, 255 rows ending t-1, min 60, ddof=1). sharp_up t > 2.0; slow_up 1.26 < t <= 2.0; flat -1.29 <= t <= 1.26; slow_dn -2.0 <= t < -1.29; sharp_dn t < -2.0. Never fires when σ_ret is NaN or below sigma_floor (0.005).';
COMMENT ON COLUMN analysis.mov_ave_price_vs_amt.vol_state IS 'Trading-amount state: z = (amt_ratio - μ) / σ where amt_ratio = trading_amount[t] / mean(trading_amount[t-5..t-1]) and μ/σ are the rolling-255 (min 60, ddof=1) moments of amt_ratio, shifted 1 row. heavy z > 2.0; normal -0.92 <= z <= 2.0; shrink z < -0.92. NULL trading_amount → no row.';
COMMENT ON COLUMN analysis.mov_ave_price_vs_amt.side IS 'Reversal side of the px_vol family: top (sharp_up/slow_up — reversal = n-day change below -reverse_threshold), bottom (slow_dn/sharp_dn — reversal above +reverse_threshold), flat (no directional claim). Mirrors analysis_forecasts.px_vol_state.side.';
COMMENT ON COLUMN analysis.mov_ave_price_vs_amt.px_t IS 'The day''s σ-standardized price speed t = ret_1d / px_sigma (recorded so consumers compute bucket mean_t without re-deriving features).';
COMMENT ON COLUMN analysis.mov_ave_price_vs_amt.px_z IS 'The day''s z-scored 量比 (recorded for the buckets'' mean_z).';
COMMENT ON COLUMN analysis.mov_ave_price_vs_amt.ret_1d IS '1-row fractional price change of the code''s own price series (COALESCE(adj_close, close) for ETF).';
COMMENT ON COLUMN analysis.mov_ave_price_vs_amt.px_sigma IS 'The σ_ret bar the day''s ret_1d was standardized by: rolling-255-row sample std (ddof=1, min_periods 60) of ret_1d, SHIFTED 1 row.';
COMMENT ON COLUMN analysis.mov_ave_price_vs_amt.amt_ratio IS 'amt_ratio (the classic 量比): trading_amount[t] / mean(trading_amount[t-5..t-1]) — today's turnover vs its own trailing 5-row mean, excluding today.';
COMMENT ON COLUMN analysis.mov_ave_price_vs_amt.sigma_window IS 'Recorded build parameter: rolling window (rows, min_periods 60, ddof=1) of σ_ret and of the amt_ratio moments. Default 255.';
COMMENT ON COLUMN analysis.mov_ave_price_vs_amt.lb_window IS 'Recorded build parameter: 量比 base window — amt_ratio = trading_amount[t] / mean(trading_amount[t-lb_window..t-1]). Default 5.';
COMMENT ON COLUMN analysis.mov_ave_price_vs_amt.k_slow_up IS 'Recorded build parameter: slow_up lower t-bar (default 1.26).';
COMMENT ON COLUMN analysis.mov_ave_price_vs_amt.k_slow_dn IS 'Recorded build parameter: slow_dn upper |t|-bar (default 1.29).';
COMMENT ON COLUMN analysis.mov_ave_price_vs_amt.k_sharp IS 'Recorded build parameter: sharp vs slow t-bar (default 2.00).';
COMMENT ON COLUMN analysis.mov_ave_price_vs_amt.z_heavy IS 'Recorded build parameter: heavy (放量) z-bar (default 2.00).';
COMMENT ON COLUMN analysis.mov_ave_price_vs_amt.z_shrink IS 'Recorded build parameter: shrink (缩量) z-bar (default -0.92).';
COMMENT ON COLUMN analysis.mov_ave_price_vs_amt.sigma_floor IS 'Recorded build parameter: minimum σ_ret for a day to join any category (default 0.005 — bond-like indices excluded).';
