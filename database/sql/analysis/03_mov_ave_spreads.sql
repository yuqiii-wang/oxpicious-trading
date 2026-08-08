-- ============================================================================
--  Table: analysis.mov_ave_spreads_detail   (WIDE: one row per asset+code+date,
--                                             9 gap columns)
--
--  Supports multiple security types via the `sec_type` column
--  ('etf' | 'index' | 'stock'). ETF prices use COALESCE(stats.etf_adjustment.adj_close,
--  stats.etf_basic_stats.close); index prices use stats.index_basic_stats.close
--  (indices have no adjustment table); stock prices use stats.stock_basic_stats.close
--  (stock_adjustment / stock_tech_stats not yet available — stock rows will be
--  populated once those tables exist). MAs come from the corresponding
--  *_tech_stats table (ma5 / ma20 / ma60 / ma120 / ma255).
--
--  9 gap pairs (canonical order):
--    5 Price-vs-MA pairs:  gap = (price - maX) / maX,  X ∈ {5,20,60,120,255}
--    4 MA5-vs-MA pairs:    gap = (ma5  - maX) / maX,  X ∈ {20,60,120,255}
--
--  Detail table stores one row per (sec_type, code, date) with all 9 gap
--  values in wide form.
--
--  Repopulated from scratch on every run of
--  `analyze_mov_ave_spread.py` (TRUNCATE then INSERT).
--
--  NOTE: Foreign-key constraints to the source OHLCV / MA tables are NOT
--  enforced because the source tables differ per sec_type (etf_* vs
--  index_* vs stock_*). Data integrity is guaranteed by the build script's
--  INNER JOINs on both basic_stats and tech_stats for each security type.
-- ============================================================================

-- Drop any prior version of these tables (ETF-only etf_mov_ave_spreads_* and
-- earlier wide-format revisions). Also remove the old analysis_identity row
-- so the new mov_ave_spread registration starts clean.
-- peaks_and_floors MUST be dropped AFTER detail (or CASCADE) because detail
-- has an FK to peaks_and_floors; CASCADE handles both orderings safely.
DROP TABLE IF EXISTS analysis.mov_ave_spreads_detail CASCADE;
DROP TABLE IF EXISTS analysis.mov_ave_peaks_and_floors CASCADE;
DROP TABLE IF EXISTS analysis.mov_ave_large_swings CASCADE;
DROP TABLE IF EXISTS analysis.mov_ave_rsi CASCADE;
DROP TABLE IF EXISTS analysis.etf_mov_ave_spreads_detail;
DELETE FROM analysis.analysis_identity WHERE name = 'etf_mov_ave_spread';
DELETE FROM analysis.analysis_identity WHERE name = 'mov_ave_rsi';

-- ----------------------------------------------------------------------------
--  Table: analysis.mov_ave_spreads_detail  (WIDE format)
--    One row per (sec_type, code, date) holding all 9 gap values.
--
--  Columns:
--    sec_type, code, date              — PK; identifies the asset universe
--                                            (etf vs index), ticker, and date
--    price_vs_ma{5,20,60,120,255}        — (price - maX) / maX
--    ma5_vs_ma{20,60,120,255}            — (ma5  - maX) / maX
--    std_{5,20,60,120,255}days            — rolling population σ of price over
--                                            N trading days (Bollinger band
--                                            width). Same units as price.
--                                            NULL until N rows are available.
--
--  Gap values are stored as signed fractional ratios (e.g. 0.05 = +5%).
--  NULL when either the numerator or denominator is NULL or non-positive.
--  σ values are in price units (not price²) so NUMERIC(10,6) holds them
--  without overflow for any realistic ETF / index / stock price.
-- ----------------------------------------------------------------------------
CREATE TABLE analysis.mov_ave_spreads_detail (
    sec_type        TEXT         NOT NULL,  -- 'etf' | 'index' | 'stock'
    code              TEXT         NOT NULL,
    date              DATE         NOT NULL,

    -- FK to analysis.mov_ave_peaks_and_floors(sec_type, code, date).
    -- "Nearest preceding extreme" mapping: for each detail row at date D,
    -- peaks_and_floors_date = the largest extreme date <= D (most recent
    -- peak/floor on or before D). NULL only when no extreme exists before
    -- D (e.g. early history before the first belt). Constrained by a
    -- DEFERRABLE FK so the build script can insert peaks_and_floors +
    -- detail in any order within a single transaction.
    peaks_and_floors_date DATE,

    -- 5 Price-vs-MA gap columns
    price_vs_ma5      NUMERIC(10,6),
    price_vs_ma20     NUMERIC(10,6),
    price_vs_ma60     NUMERIC(10,6),
    price_vs_ma120    NUMERIC(10,6),
    price_vs_ma255    NUMERIC(10,6),

    -- 4 MA5-vs-MA gap columns
    ma5_vs_ma20       NUMERIC(10,6),
    ma5_vs_ma60       NUMERIC(10,6),
    ma5_vs_ma120      NUMERIC(10,6),
    ma5_vs_ma255      NUMERIC(10,6),

    price_slope       NUMERIC(10,6), -- 1st derivative of price
    ma5_slope         NUMERIC(10,6), -- 1st derivative of MA5
    ma20_slope        NUMERIC(10,6), -- 1st derivative of MA20
    ma60_slope        NUMERIC(10,6), -- 1st derivative of MA60
    ma120_slope       NUMERIC(10,6), -- 1st derivative of MA120
    ma255_slope       NUMERIC(10,6),

    price_curvature   NUMERIC(10,6), -- 2nd derivative of price
    ma5_curvature     NUMERIC(10,6), -- 2nd derivative of MA5
    ma20_curvature    NUMERIC(10,6), -- 2nd derivative of MA20
    ma60_curvature    NUMERIC(10,6), -- 2nd derivative of MA60
    ma120_curvature   NUMERIC(10,6), -- 2nd derivative of MA120
    ma255_curvature   NUMERIC(10,6), -- 2nd derivative of MA255

    -- 5 rolling population σ columns (in price units, Bollinger band width).
    -- σ_N[t] = sqrt( mean( (price[t-N+1..t] - mean(price[t-N+1..t]))^2 ) )
    -- using ddof=0 (population std, the Bollinger convention). NULL until
    -- the rolling window is fully populated (N consecutive rows).
    std_5days         NUMERIC(10,6),
    std_20days        NUMERIC(10,6),
    std_60days        NUMERIC(10,6),
    std_120days       NUMERIC(10,6),
    std_255days       NUMERIC(10,6),

    CONSTRAINT pk_mov_ave_spreads_detail PRIMARY KEY (sec_type, code, date),
    CONSTRAINT chk_mov_ave_spreads_detail_sec_type
        CHECK (sec_type IN ('etf', 'index', 'stock'))
);

-- NOTE: no separate (sec_type, code, date) index — the PK already covers
-- that lookup. A duplicate index was previously created here and dropped
-- because it doubled index-maintenance cost on every INSERT for zero
-- benefit (PK B-tree already serves equality + range scans on the
-- (sec_type, code, date) prefix).
--
-- Index for the FK lookups + monthly group-by queries (e.g. "all detail
-- rows belonging to a given peaks_and_floors row").
CREATE INDEX idx_mov_ave_spreads_detail_pf_date
    ON analysis.mov_ave_spreads_detail (sec_type, code, peaks_and_floors_date);

COMMENT ON TABLE  analysis.mov_ave_spreads_detail              IS 'MA-spread detail (WIDE format): one row per (sec_type, code, date) with 9 gap_value columns (5 Price/MA + 4 MA5/MA). sec_type ∈ {etf, index, stock}.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.sec_type   IS 'Security type: etf (ETF), index (CSI-style index), or stock (individual equity).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.code         IS 'Ticker. ETFs use exchange suffix (e.g. "510050.SS"); indices use bare code (e.g. "000300").';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.date         IS 'Business date (trading day).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.price_vs_ma5 IS '(price - ma5) / ma5 — signed fractional gap (NULL when either is NULL/invalid).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.price_vs_ma20 IS '(price - ma20) / ma20.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.price_vs_ma60 IS '(price - ma60) / ma60.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.price_vs_ma120 IS '(price - ma120) / ma120.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.price_vs_ma255 IS '(price - ma255) / ma255.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.ma5_vs_ma20  IS '(ma5 - ma20) / ma20.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.ma5_vs_ma60  IS '(ma5 - ma60) / ma60.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.ma5_vs_ma120 IS '(ma5 - ma120) / ma120.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.ma5_vs_ma255 IS '(ma5 - ma255) / ma255.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.price_slope     IS '1st derivative of price (price[t] - price[t-1]) per trading day. NULL on the first date of each code.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.ma5_slope       IS '1st derivative of MA5 (MA5[t] - MA5[t-1]) per trading day. NULL on the first date of each code.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.ma20_slope      IS '1st derivative of MA20 (MA20[t] - MA20[t-1]).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.ma60_slope      IS '1st derivative of MA60 (MA60[t] - MA60[t-1]).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.ma120_slope     IS '1st derivative of MA120 (MA120[t] - MA120[t-1]).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.ma255_slope     IS '1st derivative of MA255 (MA255[t] - MA255[t-1]).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.price_curvature IS '2nd derivative of price (slope[t] - slope[t-1]). NULL on the first two dates of each code.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.ma5_curvature   IS '2nd derivative of MA5 (slope[t] - slope[t-1]). NULL on the first two dates of each code.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.ma20_curvature  IS '2nd derivative of MA20 (slope[t] - slope[t-1]).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.ma60_curvature  IS '2nd derivative of MA60 (slope[t] - slope[t-1]).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.ma120_curvature IS '2nd derivative of MA120 (slope[t] - slope[t-1]).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.ma255_curvature IS '2nd derivative of MA255 (slope[t] - slope[t-1]).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.std_5days   IS 'Rolling population σ (ddof=0) of price over 5 trading days. Bollinger band width for MA5 envelope. NULL until 5 consecutive rows.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.std_20days  IS 'Rolling population σ (ddof=0) of price over 20 trading days. Bollinger band width for MA20 envelope. NULL until 20 consecutive rows.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.std_60days  IS 'Rolling population σ (ddof=0) of price over 60 trading days. Bollinger band width for MA60 envelope. NULL until 60 consecutive rows.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.std_120days IS 'Rolling population σ (ddof=0) of price over 120 trading days. Bollinger band width for MA120 envelope. NULL until 120 consecutive rows.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail.std_255days IS 'Rolling population σ (ddof=0) of price over 255 trading days. Bollinger band width for MA255 envelope. NULL until 255 consecutive rows.';


-- ----------------------------------------------------------------------------
--  Table: analysis.mov_ave_peaks_and_floors  (per-EXTREME-DATE cadence)
--    One row per (sec_type, code, extreme_date). `date` IS the extreme date
--    itself — the actual biz date on which a local minimum or maximum close
--    price was observed within a "continuous belt" (see
--    analyze.mov_ave_spread.peaks_and_floors). The detail table
--    analysis.mov_ave_spreads_detail references this table via its
--    `peaks_and_floors_date` FK column using a "nearest preceding extreme"
--    mapping: each detail row at date D maps to the largest extreme date
--    <= D (NULL only when no extreme exists before D).
--
--  Columns:
--    sec_type, code, date  — PK; identifies the asset universe and the
--                            extreme biz date (NOT a month-start).
--    extreme_val           — the local min or max close price observed on
--                            `date`. Always NOT NULL (every row is an
--                            extreme). Currently only minima (valley lows)
--                            are detected; maxima will be added later.
--    nearby_extreme_date   — the furthest date within ±30 trading days of
--                            `date` (the valley_low_date) whose OHLC low is
--                            strictly lower than the valley_low's OHLC high.
--                            NULL when no qualifying date exists in the
--                            ±30 trading-day window.
--
--  Cadence: ONE row per detected extreme (trend). The build script detects
--  continuous belts (close < MA60 − 2σ, or close < MA60 for > 20 days,
--  both with < 5 day interruption bridging), merges overlapping belts into
--  trends, and emits one row per trend — the day with the min close price
--  in that trend's span. Valley_lows are then deduplicated: no two
--  surviving extremes may be within ±30 trading days of each other (the
--  min is kept when multiple fall within the window). Non-extreme dates
--  have no peaks_and_floors row; detail.peaks_and_floors_date is NULL for
--  them.
-- ----------------------------------------------------------------------------
CREATE TABLE analysis.mov_ave_peaks_and_floors (
    sec_type          TEXT         NOT NULL,  -- 'etf' | 'index' | 'stock'
    code              TEXT         NOT NULL,
    date              DATE         NOT NULL,  -- extreme biz date (NOT month-start)

    extreme_val        NUMERIC(18,6)         NOT NULL,
    nearby_extreme_date DATE,

    CONSTRAINT pk_mov_ave_peaks_and_floors PRIMARY KEY (sec_type, code, date),
    CONSTRAINT chk_mov_ave_peaks_and_floors_sec_type
        CHECK (sec_type IN ('etf', 'index', 'stock'))
);

CREATE INDEX idx_mov_ave_peaks_and_floors_sec_type_code_date
    ON analysis.mov_ave_peaks_and_floors (sec_type, code, date);

COMMENT ON TABLE  analysis.mov_ave_peaks_and_floors             IS 'Peaks-and-floors analysis: one row per (sec_type, code, extreme_date). `date` is the extreme biz date (local min/max close); detail.mov_ave_spreads_detail.peaks_and_floors_date FK references this table (NULL for non-extreme dates).';
COMMENT ON COLUMN analysis.mov_ave_peaks_and_floors.sec_type    IS 'Security type: etf (ETF), index (CSI-style index), or stock (individual equity).';
COMMENT ON COLUMN analysis.mov_ave_peaks_and_floors.code        IS 'Ticker. ETFs use exchange suffix (e.g. "510050.SS"); indices use bare code (e.g. "000300").';
COMMENT ON COLUMN analysis.mov_ave_peaks_and_floors.date        IS 'Extreme biz date — the actual trading day on which a local min/max close was observed within a continuous belt. PK column referenced by mov_ave_spreads_detail.peaks_and_floors_date.';
COMMENT ON COLUMN analysis.mov_ave_peaks_and_floors.extreme_val  IS 'The local min or max close price observed on `date`. Currently only minima (valley lows) are detected via continuous-belt logic (close < MA60 − 2σ, or close < MA60 for > 20 days, with < 5 day interruption bridging; overlapping belts merged into trends, one extreme per trend). Valley_lows are then deduplicated: no two surviving extremes within ±30 trading days (min kept).';
COMMENT ON COLUMN analysis.mov_ave_peaks_and_floors.nearby_extreme_date IS 'The furthest date within ±30 trading days of `date` (the valley_low_date) whose OHLC low is strictly lower than the valley_low''s OHLC high. NULL when no qualifying date exists in the ±30 trading-day window. Computed by analyze.mov_ave_spread.peaks_and_floors._compute_nearby_extreme_date.';

-- ----------------------------------------------------------------------------
--  Table: analysis.mov_ave_rsi  (per-asset+date Wilder RSI + short-term gaps)
--    One row per (sec_type, code, date). Stores Wilder's Relative Strength
--    Index for 4 windows (6/10/14/20 days) and 2 short-term price-gap
--    (N-day return) columns (2/3 days).
--
--  RSI formula (Wilder's smoothing — EWM with alpha = 1/N, adjust=False):
--    delta[t]   = price[t] - price[t-1]                      (per code)
--    gain[t]    = max(delta, 0);  loss[t] = max(-delta, 0)
--    avg_gain   = gain.ewm(alpha=1/N, adjust=False, min_periods=N).mean()
--    avg_loss   = loss.ewm(alpha=1/N, adjust=False, min_periods=N).mean()
--    RS         = avg_gain / avg_loss
--    RSI        = 100 - 100 / (1 + RS)
--      RSI = 100  when avg_loss = 0 and avg_gain > 0  (pure uptrend)
--      RSI = 0    when avg_gain = 0 and avg_loss > 0  (pure downtrend)
--      RSI = NULL when avg_gain = 0 and avg_loss = 0  (flat / undefined)
--    NULL until N consecutive gain/loss observations are available
--    (min_periods=N). Range: 0..100.
--
--  Gap formula (N-day price return):
--    gap_Ndays[t] = (price[t] - price[t-N]) / price[t-N]   (per code)
--    NULL until N prior rows exist (first N rows per code are NULL).
--    Signed fractional ratio (e.g. 0.05 = +5%).
--
--  Source prices match mov_ave_spreads_detail: ETF uses
--  COALESCE(etf_adjustment.adj_close, etf_basic_stats.close); index uses
--  index_basic_stats.close; stock uses stock_basic_stats.close.
--
--  Populated by `analyze.mov_ave_spread` (internal RSI step in rsi.py;
--  incremental upsert by missing dates; --force truncates first). No FK to
--  mov_ave_spreads_detail — data integrity is guaranteed by INNER JOINs on
--  basic_stats in the build script.
-- ----------------------------------------------------------------------------
CREATE TABLE analysis.mov_ave_rsi (
    sec_type        TEXT         NOT NULL,  -- 'etf' | 'index' | 'stock'
    code            TEXT         NOT NULL,
    date            DATE         NOT NULL,

    -- 4 Wilder RSI columns (0..100, NULL until N periods)
    rsi_6days       NUMERIC(10,6),
    rsi_10days      NUMERIC(10,6),
    rsi_14days      NUMERIC(10,6),
    rsi_20days      NUMERIC(10,6),

    -- 2 short-term price-gap (N-day return) columns
    gap_2days       NUMERIC(10,6), -- sign indicates last extreme is max or min
    gap_3days       NUMERIC(10,6), -- sign indicates last extreme is max or min 
    gap_since_last_extreme NUMERIC(10,6), -- sign indicates last extreme is max or min
    days_since_last_extreme NUMERIC(10,6),
    date_of_last_extreme DATE,

    CONSTRAINT pk_mov_ave_rsi PRIMARY KEY (sec_type, code, date),
    CONSTRAINT chk_mov_ave_rsi_sec_type
        CHECK (sec_type IN ('etf', 'index', 'stock'))
);

CREATE INDEX idx_mov_ave_rsi_sec_type_code_date
    ON analysis.mov_ave_rsi (sec_type, code, date);

COMMENT ON TABLE  analysis.mov_ave_rsi             IS 'Wilder RSI (6/10/14/20 days) + short-term price gaps (2/3 day returns). One row per (sec_type, code, date). sec_type ∈ {etf, index, stock}.';
COMMENT ON COLUMN analysis.mov_ave_rsi.sec_type    IS 'Security type: etf (ETF), index (CSI-style index), or stock (individual equity).';
COMMENT ON COLUMN analysis.mov_ave_rsi.code        IS 'Ticker. ETFs use exchange suffix (e.g. "510050.SS"); indices use bare code (e.g. "000300").';
COMMENT ON COLUMN analysis.mov_ave_rsi.date        IS 'Business date (trading day).';
COMMENT ON COLUMN analysis.mov_ave_rsi.rsi_6days   IS 'Wilder RSI over 6 trading days (alpha=1/6, ewm adjust=False, min_periods=6). 0..100. NULL until 6 consecutive gain/loss observations.';
COMMENT ON COLUMN analysis.mov_ave_rsi.rsi_10days  IS 'Wilder RSI over 10 trading days (alpha=1/10, ewm adjust=False, min_periods=10). 0..100. NULL until 10 periods.';
COMMENT ON COLUMN analysis.mov_ave_rsi.rsi_14days  IS 'Wilder RSI over 14 trading days (alpha=1/14, ewm adjust=False, min_periods=14) — the classic Wilder window. 0..100. NULL until 14 periods.';
COMMENT ON COLUMN analysis.mov_ave_rsi.rsi_20days  IS 'Wilder RSI over 20 trading days (alpha=1/20, ewm adjust=False, min_periods=20). 0..100. NULL until 20 periods.';
COMMENT ON COLUMN analysis.mov_ave_rsi.gap_2days   IS '2-day price return: (price[t] - price[t-2]) / price[t-2]. Signed fractional ratio. NULL for the first 2 rows of each code.';
COMMENT ON COLUMN analysis.mov_ave_rsi.gap_3days   IS '3-day price return: (price[t] - price[t-3]) / price[t-3]. Signed fractional ratio. NULL for the first 3 rows of each code.';
COMMENT ON COLUMN analysis.mov_ave_rsi.gap_since_last_extreme IS 'Signed fractional gap from the most recent local turning point (high/low) detected by price_slope sign change: (price[t] - extreme_price) / extreme_price. Sign indicates the type of the last extreme: positive = last extreme was a local MIN (price rebounded upward since the trough), negative = last extreme was a local MAX (price fell since the peak). NULL when no preceding turning point exists for the code (early history before the first turn).';
COMMENT ON COLUMN analysis.mov_ave_rsi.days_since_last_extreme IS 'Trading days since the most recent local turning point (high/low) detected by price_slope sign change. 0 on the extreme row itself. NULL when no preceding turning point exists for the code.';
COMMENT ON COLUMN analysis.mov_ave_rsi.date_of_last_extreme IS 'The biz date of the most recent local turning point (high/low) detected by price_slope sign change — i.e. the date on which the extreme_price referenced by gap_since_last_extreme was observed. Carried forward (forward-filled) from each turning point until the next one. NULL when no preceding turning point exists for the code (early history before the first turn).';
