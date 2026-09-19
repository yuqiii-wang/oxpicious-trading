-- ============================================================================
--  mov_ave_spread suite — split of the former 03_mov_ave_spreads.sql.
--  Shared conventions (sec_type handling, the NO-FK derived-data design,
--  rebuild semantics): see 01_mov_ave_spreads_detail.sql.
-- ============================================================================

-- ----------------------------------------------------------------------------
--  Table: analysis.mov_ave_trading_amt_ratios  (liquidity-impact ratios)
--    One row per (sec_type, code, date). Capital-per-movement ratios:
--    how many MILLIONS of yuan of trading amount accompany one unit of
--    price movement. This is the reciprocal of the Amihud (2002)
--    illiquidity measure (ILLIQ = |price change| / dollar volume): a
--    HIGH value means a deep market (much capital absorbed per unit of
--    movement); a LOW value means a thin market (small capital moved
--    price a lot).
--
--  Financial semantics — the daily price move decomposes into three
--  measurable legs, and each leg gets its own capital ratio:
--
--    close[t] - close[t-1]  (net daily move)      = price_slope
--    open[t]  - close[t-1]  (overnight gap)       — jump between sessions
--    high[t]  - low[t]     (intraday range)       — session movement envelope
--
--  1. Slope ratios (signed, close-to-close basis), NUMERIC(10,4):
--     trading_amt_vs_price_slope_ratio
--         = (trading_amount / 1M) / price_slope
--         today's capital per unit of NET daily price change.
--     trading_amt_ma{W}_vs_price_ma{W}_slope_ratio
--         = (trading_amt_ma{W} / 1M) / ma{W}_slope
--         matching-timescale: W-day average capital per unit of the W-day
--         price-MA daily step (trend liquidity — the capital behind the
--         average trend step, smoothed).
--
--  2. Range ratio (unsigned — intraday depth gauge), NUMERIC(10,4):
--     trading_amt_vs_high_low_ratio
--         = (trading_amount / 1M) / (high - low)
--         capital per unit of intraday range. Range-based liquidity in the
--         Parkinson-volatility spirit: high turnover + narrow range = deep
--         book; low turnover + wide range = volatile / thin session.
--
--  3. Overnight-gap ratio (signed — gap-day liquidity), NUMERIC(10,4):
--     trading_amt_vs_overnight_gap_ratio
--         = (trading_amount / 1M) / (open[t] - close[t-1])
--         capital traded on a day that gapped, per unit of gap. NOTE: the
--         draft name "gap betw prev close today close" would be literally
--         identical to price_slope (close[t] - close[t-1]); it is
--         implemented as the standard trading "gap" instead — where
--         today's session OPENS relative to yesterday's close — which is
--         the distinct, non-redundant quantity.
--
--  4. MA5 versions of 2 & 3 (matching timescale), NUMERIC(10,4):
--     trading_amt_ma5_vs_high_low_ma5_ratio
--         = (trading_amt_ma5 / 1M) / MA5(high - low)
--     trading_amt_ma5_vs_overnight_gap_ma5_ratio
--         = (trading_amt_ma5 / 1M) / MA5(open[t] - close[t-1])
--         5-day average capital per unit of 5-day average range / gap.
--
--  Sign convention: turnover >= 0, so sign(ratio) = sign(movement).
--  Negative slope/gap ratios = the move was downward.
--
--  Zero-movement guard (all columns): a 0 denominator is auto-set to 1.0
--  (the stored value then equals the capital in millions — a pragmatic
--  floor for flat / limit-locked days rather than a true ratio).
--
--  Scale note: ratios are in price units (not scale-free), so absolute
--  values are comparable across time within a code, and cross-sectionally
--  only among similarly-priced instruments. Typical magnitudes: 10^2
--  (small stocks) .. 10^5 (broad indices / liquid ETFs); values with
--  |v| >= 10^6 (the NUMERIC(10,4) cap) are nulled by the build script.
--
--  Populated by the internal trading-amt-ratios step of
--  `analyze.mov_ave_spread` (see trading_amt_ratios.py). Incremental
--  upsert by missing dates; --force truncates first.
-- ----------------------------------------------------------------------------

CREATE TABLE analysis.mov_ave_trading_amt_ratios (
    sec_type        TEXT         NOT NULL,  -- 'etf' | 'index' | 'stock'
    code            TEXT         NOT NULL,
    date            DATE         NOT NULL,

    -- 6 slope ratios (signed, close-to-close basis; matching timescale)
    trading_amt_vs_price_slope_ratio             NUMERIC(10,4),
    trading_amt_ma5_vs_price_ma5_slope_ratio     NUMERIC(10,4),
    trading_amt_ma20_vs_price_ma20_slope_ratio   NUMERIC(10,4),
    trading_amt_ma60_vs_price_ma60_slope_ratio   NUMERIC(10,4),
    trading_amt_ma120_vs_price_ma120_slope_ratio NUMERIC(10,4),
    trading_amt_ma255_vs_price_ma255_slope_ratio NUMERIC(10,4),

    -- 4 range / overnight-gap ratios
    trading_amt_vs_high_low_ratio                NUMERIC(10,4),
    trading_amt_vs_overnight_gap_ratio           NUMERIC(10,4),
    trading_amt_ma5_vs_high_low_ma5_ratio        NUMERIC(10,4),
    trading_amt_ma5_vs_overnight_gap_ma5_ratio   NUMERIC(10,4),

    CONSTRAINT pk_mov_ave_trading_amt_ratios PRIMARY KEY (code, sec_type, date)
) PARTITION BY HASH (code);

-- Native hash partitions (16) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p15
SELECT public.create_hash_partitions('analysis', 'mov_ave_trading_amt_ratios', 16);

COMMENT ON TABLE  analysis.mov_ave_trading_amt_ratios              IS 'Liquidity-impact ratios (WIDE format): one row per (sec_type, code, date) with 10 capital-per-movement ratio columns — 6 slope ratios ((trading_amt or trading_amt_maW) / 1M yuan) / (price_slope or maW_slope), matching-timescale) + range ratio ((trading_amt / 1M) / (high - low)) + overnight-gap ratio ((trading_amt / 1M) / (open - prev close)) + MA5 versions of both. Reciprocal of the Amihud illiquidity measure: higher = deeper market. sec_type ∈ {etf, index, stock}. Built by analyze.mov_ave_spread (trading_amt_ratios.py).';
COMMENT ON COLUMN analysis.mov_ave_trading_amt_ratios.sec_type   IS 'Security type: etf (ETF), index (CSI-style index), or stock (individual equity).';
COMMENT ON COLUMN analysis.mov_ave_trading_amt_ratios.code         IS 'Ticker. ETFs use exchange suffix (e.g. "510050.SS"); indices use bare code (e.g. "000300").';
COMMENT ON COLUMN analysis.mov_ave_trading_amt_ratios.date         IS 'Business date (trading day).';
COMMENT ON COLUMN analysis.mov_ave_trading_amt_ratios.trading_amt_vs_price_slope_ratio       IS 'Liquidity-impact proxy: (trading_amount / 1,000,000) / price_slope — today''s capital (millions of yuan) per unit of NET close-to-close price change. Reciprocal of the Amihud illiquidity measure: higher = deeper market. Signed: negative = the daily move was downward. price_slope=0 auto-set to 1.0 (stored value = capital in millions). NULL when trading_amount or price_slope is NULL.';
COMMENT ON COLUMN analysis.mov_ave_trading_amt_ratios.trading_amt_ma5_vs_price_ma5_slope_ratio   IS '(trading_amt_ma5 / 1,000,000) / ma5_slope. Millions of capital (MA5 timescale) per unit of MA5 price change — trend liquidity. ma5_slope=0 auto-set to 1.0.';
COMMENT ON COLUMN analysis.mov_ave_trading_amt_ratios.trading_amt_ma20_vs_price_ma20_slope_ratio  IS '(trading_amt_ma20 / 1,000,000) / ma20_slope. Millions of capital (MA20 timescale) per unit of MA20 price change. ma20_slope=0 auto-set to 1.0.';
COMMENT ON COLUMN analysis.mov_ave_trading_amt_ratios.trading_amt_ma60_vs_price_ma60_slope_ratio  IS '(trading_amt_ma60 / 1,000,000) / ma60_slope. Millions of capital (MA60 timescale) per unit of MA60 price change. ma60_slope=0 auto-set to 1.0.';
COMMENT ON COLUMN analysis.mov_ave_trading_amt_ratios.trading_amt_ma120_vs_price_ma120_slope_ratio IS '(trading_amt_ma120 / 1,000,000) / ma120_slope. Millions of capital (MA120 timescale) per unit of MA120 price change. ma120_slope=0 auto-set to 1.0.';
COMMENT ON COLUMN analysis.mov_ave_trading_amt_ratios.trading_amt_ma255_vs_price_ma255_slope_ratio IS '(trading_amt_ma255 / 1,000,000) / ma255_slope. Millions of capital (MA255 timescale) per unit of MA255 price change. ma255_slope=0 auto-set to 1.0.';
COMMENT ON COLUMN analysis.mov_ave_trading_amt_ratios.trading_amt_vs_high_low_ratio                IS '(trading_amount / 1,000,000) / (high - low) — capital per unit of INTRADAY range (unsigned, always positive). Range-based liquidity: high turnover + narrow range = deep book (Parkinson-volatility spirit); low turnover + wide range = volatile / thin session. range=0 (limit-locked / flat day) auto-set denominator to 1.0 (stored value = capital in millions). NULL when trading_amount, high, or low is NULL.';
COMMENT ON COLUMN analysis.mov_ave_trading_amt_ratios.trading_amt_vs_overnight_gap_ratio           IS '(trading_amount / 1,000,000) / (open[t] - close[t-1]) — capital traded on a day that gapped, per unit of OVERNIGHT gap (where today''s session opens relative to yesterday''s close; the literal "prev close vs today close" reading would be identical to trading_amt_vs_price_slope_ratio). Signed: negative = gap down. Gap-day liquidity / confirmation gauge: high ratio on a big gap = the gap attracted flow; low ratio = thin overnight market. gap=0 auto-set denominator to 1.0. NULL on the first date per code or when trading_amount, open, or prev close is NULL.';
COMMENT ON COLUMN analysis.mov_ave_trading_amt_ratios.trading_amt_ma5_vs_high_low_ma5_ratio        IS '(trading_amt_ma5 / 1,000,000) / MA5(high - low) — matching timescale: 5-day average capital per unit of 5-day average daily range. NULL until 5 consecutive rows.';
COMMENT ON COLUMN analysis.mov_ave_trading_amt_ratios.trading_amt_ma5_vs_overnight_gap_ma5_ratio   IS '(trading_amt_ma5 / 1,000,000) / MA5(open[t] - close[t-1]) — matching timescale: 5-day average capital per unit of 5-day average overnight gap. NULL until 5 consecutive gap observations.';
