-- ============================================================================
--  Margin Changes — per-(sec_type, code, trend) summary of SIGNIFICANT margin
--  balance TRENDS (sustained UP or DOWN moves) on the RONGZI (融资 /
--  cash-borrow) margin balance curve.
--
--  PURPOSE
--    A "margin change" row is a single TREND EPISODE on the rz_balance
--    series: a contiguous span [start_date, end_date] during which the
--    rongzi outstanding balance moved persistently in one direction
--    (UP = balance accumulating / traders adding leveraged longs, or
--    DOWN = balance unwinding / traders cutting leverage). Each row
--    summarizes the episode with:
--      days_of_trend            — span length in trading days
--      is_trend_up_not_down     — direction (TRUE = accumulation, FALSE = unwind)
--      netting_buy              — (Δbalance) − (Σ rz_buy over the span).
--                                 Decomposes the balance move into the
--                                 part explained by fresh rongzi BUY flow
--                                 vs the residual (non-buy balance drivers:
--                                 interest accrual, forced repayments,
--                                 settlements). Can be NEGATIVE when the
--                                 balance DECREASED by more than the buy
--                                 flow added (net unwinding).
--      rsi_trend                — Wilder RSI of the rz_balance series
--                                 computed over the trend window
--                                 (0..100; >70 = balance stretched up,
--                                 <30 = balance stretched down).
--      ratio_rsi_margin_vs_price — margin RSI / price RSI over the same
--                                 window. >1 = margin momentum is running
--                                 HOTTER than price momentum (leverage
--                                 leading price); <1 = margin lagging
--                                 price. Price RSI is sourced from
--                                 analysis.mov_ave_rsi (matched by
--                                 sec_type, code, date).
--
--  SCOPE — RONGZI ONLY (融资, cash borrow to buy). RONQIN (融券, sec borrow
--  to short) is INTENTIONALLY EXCLUDED per spec — only rz_balance / rz_buy
--  are used.
--
--  TREND DETECTION (slope-based direction + zscore magnitude significance)
--    Segmentation signal: margin_balance_slope_ma5 sign
--    (slope_ma5 > 0 = UP, slope_ma5 < 0 = DN). Direction comes from the
--    ACTUAL balance movement (5-day smoothed slope sign).
--
--    GAP BRIDGING: short opposite-direction runs of <= 3 days between two
--    same-direction runs are absorbed (flipped to match), preventing
--    single-day noise from fragmenting meaningful trends.
--
--    SIGNIFICANCE FILTER: a trend is KEPT only if a MAJORITY (>50%) of its
--    days have |zscore_20d| > 0 (statistically significant slope vs 20d
--    history). The zscore SIGN is NOT checked against direction — only
--    its MAGNITUDE. A declining balance can have zscore > 0 when today's
--    decline is smaller than the 20d average; that is still significant,
--    just "less negative". Direction comes from slope_ma5 sign.
--    Min trend length: 3 trading days.
--
--  Table: analysis.margin_changes
--    PK: (code, sec_type, start_date, end_date)
--    sec_type ∈ ('etf' | 'stock' | 'index')  — 'index' rows aggregated
--    from the margin_index_series VIEW (same convention as
--    margin_tech_stats).
--
--  POPULATION
--    analyze.margins (Python module). Per project rule, ALL INSERTs are
--    in Python — no raw INSERT...SELECT SQL in this file. The population
--    step reuses the in-memory rz_balance / rz_buy histories + tech_stats
--    already fetched by the pipeline (no extra DB round-trip for source
--    data). Truncate-then-recompute on every run (episode boundaries
--    shift when new dates arrive).
--
--  Register in analysis.analysis_identity (name='margin_changes').
-- ============================================================================
CREATE TABLE IF NOT EXISTS analysis.margin_changes (
    code                       TEXT          NOT NULL,
    sec_type                   TEXT          NOT NULL,  -- 'etf' | 'stock' | 'index'

    start_date                 DATE          NOT NULL,
    end_date                   DATE          NOT NULL,
    days_of_trend              INTEGER       NOT NULL,

    is_trend_up_not_down       BOOLEAN       NOT NULL,

    -- (Δbalance) − (Σ rz_buy over [start_date, end_date]). Yuan. Can be
    -- NEGATIVE for balance decrease. Decomposes the balance move into the
    -- buy-flow-explained part vs the residual (interest / repayments).
    -- NUMERIC(24,4) — balance values are in yuan and can be very large
    -- (NUMERIC(10,2) overflows at ~100M, well below real balance levels).
    netting_buy                NUMERIC(24,4),

    -- Wilder RSI of rz_balance over the trend window (0..100).
    rsi_trend                  NUMERIC(8,4),

    open_margin_balance                 NUMERIC(24,4),
    high_margin_balance                 NUMERIC(24,4),
    low_margin_balance                 NUMERIC(24,4),
    close_margin_balance                NUMERIC(24,4),

    -- margin RSI / price RSI over the same window. >1 = margin momentum
    -- hotter than price momentum (leverage leading); <1 = margin lagging.
    -- Price RSI sourced from analysis.mov_ave_rsi (matched by
    -- sec_type, code, date). NULL when price RSI is unavailable for the
    -- window (e.g. index codes not in mov_ave_rsi) or when price RSI is
    -- 0 (NULLIF guard).
    ratio_rsi_margin_vs_price  NUMERIC(10,4),
    ratio_open_margin_vs_price  NUMERIC(24,4),
    ratio_high_margin_vs_price  NUMERIC(24,4),
    ratio_low_margin_vs_price  NUMERIC(24,4),
    ratio_close_margin_vs_price  NUMERIC(24,4),

    CONSTRAINT pk_margin_changes PRIMARY KEY (code, sec_type, start_date, end_date),
    CONSTRAINT chk_margin_changes_sec_type
        CHECK (sec_type IN ('etf', 'stock', 'index')),
    -- A trend span must be non-empty and ordered.
    CONSTRAINT chk_margin_changes_date_order
        CHECK (start_date <= end_date),
    -- A trend must span at least one trading day.
    CONSTRAINT chk_margin_changes_days_positive
        CHECK (days_of_trend >= 1)
);

-- Idempotent migration: CREATE TABLE IF NOT EXISTS does not retro-fit
-- columns / constraints to an already-existing table, so ADD COLUMN IF
-- NOT EXISTS + DROP+ADD CONSTRAINT are required for production upgrades.
-- No-op on fresh installs.
ALTER TABLE analysis.margin_changes ADD COLUMN IF NOT EXISTS netting_buy               NUMERIC(24,4);
ALTER TABLE analysis.margin_changes ADD COLUMN IF NOT EXISTS rsi_trend                 NUMERIC(8,4);
ALTER TABLE analysis.margin_changes ADD COLUMN IF NOT EXISTS ratio_rsi_margin_vs_price NUMERIC(10,4);
-- OHLC margin balance per trend episode (yuan).
ALTER TABLE analysis.margin_changes ADD COLUMN IF NOT EXISTS open_margin_balance        NUMERIC(24,4);
ALTER TABLE analysis.margin_changes ADD COLUMN IF NOT EXISTS high_margin_balance        NUMERIC(24,4);
ALTER TABLE analysis.margin_changes ADD COLUMN IF NOT EXISTS low_margin_balance         NUMERIC(24,4);
ALTER TABLE analysis.margin_changes ADD COLUMN IF NOT EXISTS close_margin_balance       NUMERIC(24,4);
-- OHLC margin / price ratios (dimensionless — yuan / yuan-per-share).
ALTER TABLE analysis.margin_changes ADD COLUMN IF NOT EXISTS ratio_open_margin_vs_price  NUMERIC(24,4);
ALTER TABLE analysis.margin_changes ADD COLUMN IF NOT EXISTS ratio_high_margin_vs_price  NUMERIC(24,4);
ALTER TABLE analysis.margin_changes ADD COLUMN IF NOT EXISTS ratio_low_margin_vs_price   NUMERIC(24,4);
ALTER TABLE analysis.margin_changes ADD COLUMN IF NOT EXISTS ratio_close_margin_vs_price NUMERIC(24,4);
-- Migrate the sec_type CHECK constraint (safe to re-run).
ALTER TABLE analysis.margin_changes DROP CONSTRAINT IF EXISTS chk_margin_changes_sec_type;
ALTER TABLE analysis.margin_changes
    ADD CONSTRAINT chk_margin_changes_sec_type
        CHECK (sec_type IN ('etf', 'stock', 'index'));
ALTER TABLE analysis.margin_changes DROP CONSTRAINT IF EXISTS chk_margin_changes_date_order;
ALTER TABLE analysis.margin_changes
    ADD CONSTRAINT chk_margin_changes_date_order
        CHECK (start_date <= end_date);
ALTER TABLE analysis.margin_changes DROP CONSTRAINT IF EXISTS chk_margin_changes_days_positive;
ALTER TABLE analysis.margin_changes
    ADD CONSTRAINT chk_margin_changes_days_positive
        CHECK (days_of_trend >= 1);

-- Indexes for the common access patterns:
--   1. Per-security time series of trends (drives per-code margin trend lists).
--   2. Per-date snapshot (which trends were active / ended on a given date).
--   3. sec_type-scoped scan (incremental upsert / truncate by sec_type).
-- The PK already covers (code, sec_type, start_date, end_date) equality +
-- range scans, so no duplicate index on that prefix.
CREATE INDEX IF NOT EXISTS idx_margin_changes_sec_type_code_dates
    ON analysis.margin_changes (sec_type, code, start_date, end_date);
CREATE INDEX IF NOT EXISTS idx_margin_changes_end_date
    ON analysis.margin_changes (end_date);
CREATE INDEX IF NOT EXISTS idx_margin_changes_sec_type_end_date
    ON analysis.margin_changes (sec_type, end_date);

COMMENT ON TABLE  analysis.margin_changes                       IS 'Per-(sec_type, code, trend) summary of SIGNIFICANT margin balance TRENDS (sustained UP or DOWN moves) on the RONGZI (融资 / cash-borrow) margin balance curve. One row per trend episode: [start_date, end_date] span with direction (is_trend_up_not_down), span length (days_of_trend > 2), netting_buy ((Δbalance) − (Σ rz_buy) — decomposes the balance move into buy-flow-explained vs residual), rsi_trend (Wilder RSI of rz_balance over the window, 0..100), and ratio_rsi_margin_vs_price (margin RSI / price RSI — >1 = leverage leading price, <1 = lagging; price RSI from analysis.mov_ave_rsi). sec_type ∈ {etf, stock, index} — ''index'' rows aggregated from the margin_index_series VIEW. RONQIN (融券 / sec borrow) EXCLUDED. Trend detection: contiguous run of same-sign 5-day smoothed balance slope (margin_balance_slope_ma5 > 0 = UP, < 0 = DOWN), min 3 days. DIRECTION from slope_ma5 sign (actual balance movement). GAP BRIDGING: short opposite-direction runs of <= 3 days between two same-direction runs are absorbed. SIGNIFICANCE FILTER: MAJORITY (>50%) of trend days must have |zscore_20d| > 0 (statistically significant slope). Built by analyze.margins (truncate-then-recompute); all INSERTs in Python per project rule.';
COMMENT ON COLUMN analysis.margin_changes.code                  IS 'Security ticker with exchange suffix, e.g. "159001.SZ" (ETF) or "600008.SS" (stock). For sec_type=''index'': bare 6-digit index code (e.g. 000300) whose margin series is aggregated from the margin_index_series VIEW.';
COMMENT ON COLUMN analysis.margin_changes.sec_type             IS 'Subject security type: etf, stock, or index. ''index'' rows are aggregated from the analysis.margin_index_series VIEW (weighted-avg of constituent stocks'' rz_balance); same convention as margin_tech_stats.';
COMMENT ON COLUMN analysis.margin_changes.start_date           IS 'Trend episode start date (inclusive). First date of the sustained UP or DOWN move on the rz_balance series.';
COMMENT ON COLUMN analysis.margin_changes.end_date             IS 'Trend episode end date (inclusive). Last date of the sustained move. Must be >= start_date (CHECK chk_margin_changes_date_order).';
COMMENT ON COLUMN analysis.margin_changes.days_of_trend        IS 'Span length in TRADING days (inclusive of both endpoints). Must be >= 1 (CHECK chk_margin_changes_days_positive).';
COMMENT ON COLUMN analysis.margin_changes.is_trend_up_not_down IS 'Trend direction. TRUE = balance ACCUMULATING (traders adding leveraged longs — rz_balance rising). FALSE = balance UNWINDING (traders cutting leverage — rz_balance falling).';
COMMENT ON COLUMN analysis.margin_changes.netting_buy         IS '(Δbalance) − (Σ rz_buy over [start_date, end_date]). Yuan. Decomposes the balance move: the part explained by fresh rongzi BUY flow vs the residual (non-buy balance drivers: interest accrual, forced repayments, settlements). POSITIVE when balance grew faster than buy flow alone would explain (e.g. accrued interest inflating the balance); NEGATIVE when balance decreased by more than buy flow added (net unwinding — repayments exceeding new buys). NUMERIC(24,4) — balance values are in yuan and can be very large.';
COMMENT ON COLUMN analysis.margin_changes.rsi_trend            IS 'Wilder RSI of the rz_balance series computed over the trend window. Range 0..100. >70 = balance stretched up (overbought leverage — potential unwind risk); <30 = balance stretched down (oversold — potential re-accumulation). NULL when the window has insufficient non-NaN balance changes to compute RSI.';
COMMENT ON COLUMN analysis.margin_changes.ratio_rsi_margin_vs_price IS 'margin RSI / price RSI over the same trend window. >1 = margin momentum HOTTER than price momentum (leverage leading price — traders adding leverage ahead of / faster than price moves); <1 = margin LAGGING price (price moving without proportional leverage participation). Price RSI sourced from analysis.mov_ave_rsi (matched by sec_type, code, date). NULL when price RSI is unavailable for the window (e.g. index codes not in mov_ave_rsi) or when price RSI is 0 (NULLIF guard). NUMERIC(10,4).';
COMMENT ON COLUMN analysis.margin_changes.open_margin_balance  IS 'rz_balance on the trend start_date (first trading day of the episode). Yuan. The margin balance at the OPEN of the trend window. NUMERIC(24,4).';
COMMENT ON COLUMN analysis.margin_changes.high_margin_balance  IS 'MAX(rz_balance) over the trend window [start_date, end_date]. Yuan. The PEAK margin balance during the trend. NUMERIC(24,4).';
COMMENT ON COLUMN analysis.margin_changes.low_margin_balance   IS 'MIN(rz_balance) over the trend window [start_date, end_date]. Yuan. The TROUGH margin balance during the trend. NUMERIC(24,4).';
COMMENT ON COLUMN analysis.margin_changes.close_margin_balance IS 'rz_balance on the trend end_date (last trading day of the episode). Yuan. The margin balance at the CLOSE of the trend window. NUMERIC(24,4).';
COMMENT ON COLUMN analysis.margin_changes.ratio_open_margin_vs_price  IS 'open_margin_balance / open_price (open price on start_date). Represents shares-equivalent of leverage at the trend open price. NULL when price is unavailable or 0. NUMERIC(24,4).';
COMMENT ON COLUMN analysis.margin_changes.ratio_high_margin_vs_price  IS 'high_margin_balance / high_price (MAX high price over the window). Shares-equivalent at the peak price. NULL when price is unavailable or 0. NUMERIC(24,4).';
COMMENT ON COLUMN analysis.margin_changes.ratio_low_margin_vs_price   IS 'low_margin_balance / low_price (MIN low price over the window). Shares-equivalent at the trough price. NULL when price is unavailable or 0. NUMERIC(24,4).';
COMMENT ON COLUMN analysis.margin_changes.ratio_close_margin_vs_price IS 'close_margin_balance / close_price (close price on end_date). Shares-equivalent of leverage at the trend close price. NULL when price is unavailable or 0. NUMERIC(24,4).';

-- ----------------------------------------------------------------------------
--  Register in analysis.analysis_identity
-- ----------------------------------------------------------------------------
INSERT INTO analysis.analysis_identity (name, detail_name, summary_name, last_run_datetime, description) VALUES
    ('margin_changes', 'margin_changes', NULL, NOW(),
     'Per-(sec_type, code, trend) summary of SIGNIFICANT margin balance TRENDS (sustained UP or DOWN moves) on the RONGZI (融资 / cash-borrow) margin balance curve. One row per trend episode: [start_date, end_date] span with direction (is_trend_up_not_down), span length (days_of_trend > 2), netting_buy ((Δbalance) − (Σ rz_buy) — decomposes the balance move into buy-flow-explained vs residual), rsi_trend (Wilder RSI of rz_balance over the window, 0..100), ratio_rsi_margin_vs_price (margin RSI / price RSI — >1 = leverage leading price; price RSI from analysis.mov_ave_rsi). sec_type ∈ {etf, stock, index} — ''index'' rows aggregated from margin_index_series VIEW. RONQIN (融券 / sec borrow) EXCLUDED. Trend detection: contiguous run of same-sign 5-day smoothed balance slope (margin_balance_slope_ma5 > 0 = UP, < 0 = DOWN), min 3 days. DIRECTION from slope_ma5 sign. GAP BRIDGING: short opposite-direction runs of <= 3 days between two same-direction runs are absorbed. SIGNIFICANCE FILTER: MAJORITY (>50%) of trend days must have |zscore_20d| > 0. Built by analyze.margins (truncate-then-recompute); all INSERTs in Python per project rule.')
ON CONFLICT (name) DO UPDATE SET
    detail_name       = EXCLUDED.detail_name,
    summary_name      = EXCLUDED.summary_name,
    last_run_datetime = NOW(),
    description       = EXCLUDED.description;

-- ============================================================================
--  Margin Hype-to-Price Forcasts — per-(sec_type, code, trend) FORWARD price
--  performance after a margin trend episode ends. Validates whether margin
--  balance trends (UP/DOWN) are predictive of future price moves.
--
--  PURPOSE
--    A "forcast" row answers: after a margin trend episode [start_date,
--    end_date] ends, what did the PRICE do in the next 5, 20, and 60
--    TRADING days? For each horizon window starting from end_date+1:
--      high_price_Xd   — MAX(close) over the X-day window.
--      low_price_Xd    — MIN(close) over the X-day window.
--      days_to_high_Xd — trading days from window start to reach the high.
--      days_to_low_Xd  — trading days from window start to reach the low.
--
--    This provides a BACKTEST / VALIDATION layer for the hype signal:
--    if margin UP trends consistently see price highs early in the window
--    (small days_to_high_5d), the hype signal has predictive value.
--
--  SCOPE — RONGZI ONLY (matches margin_changes). Covers all sec_types
--  (etf, stock, index). Forward windows are computed from price close
--  data in stats.{index,etf,stock}_basic_stats.
--
--  Table: analysis.margin_hype_to_price_forcasts
--    PK/FK: (code, sec_type, start_date, end_date) → margin_changes
--    1 row per trend episode (1:1 with margin_changes).
--
--  POPULATION
--    analyze.margins.changes.forcasts (Python module). Reuses the
--    margin_changes episodes already in memory + fetches forward price
--    data from basic_stats. Truncate-then-recompute (aligned with
--    margin_changes recompute cycle).
--
--  Register in analysis.analysis_identity (name='margin_hype_to_price_forcasts').
-- ============================================================================
CREATE TABLE IF NOT EXISTS analysis.margin_hype_to_price_forcasts (
    code        TEXT NOT NULL,
    sec_type    TEXT NOT NULL,
    start_date  DATE NOT NULL,
    end_date    DATE NOT NULL,

    -- ---- 5-day forward window (end_date+1 to end_date+5) ------------------
    high_price_5d    NUMERIC(12,4),
    low_price_5d     NUMERIC(12,4),
    days_to_high_5d  INTEGER,
    days_to_low_5d   INTEGER,

    -- ---- 20-day forward window (end_date+1 to end_date+20) ---------------
    high_price_20d   NUMERIC(12,4),
    low_price_20d    NUMERIC(12,4),
    days_to_high_20d INTEGER,
    days_to_low_20d  INTEGER,

    -- ---- 60-day forward window (end_date+1 to end_date+60) ---------------
    high_price_60d   NUMERIC(12,4),
    low_price_60d    NUMERIC(12,4),
    days_to_high_60d INTEGER,
    days_to_low_60d  INTEGER,

    CONSTRAINT pk_margin_hype_to_price_forcasts
        PRIMARY KEY (code, sec_type, start_date, end_date),
    CONSTRAINT fk_margin_hype_to_price_forcasts_trend
        FOREIGN KEY (code, sec_type, start_date, end_date)
        REFERENCES analysis.margin_changes (code, sec_type, start_date, end_date),
    CONSTRAINT chk_margin_hype_to_price_forcasts_sec_type
        CHECK (sec_type IN ('etf', 'stock', 'index')),
    CONSTRAINT chk_margin_hype_to_price_forcasts_days_to_positive
        CHECK (
            days_to_high_5d IS NULL OR days_to_high_5d >= 0
        ),
    CONSTRAINT chk_margin_hype_to_price_forcasts_high_ge_low
        CHECK (
            high_price_5d IS NULL OR low_price_5d IS NULL
            OR high_price_5d >= low_price_5d
        )
);

-- Idempotent migration: ADD COLUMN IF NOT EXISTS for forward-compatibility.
ALTER TABLE analysis.margin_hype_to_price_forcasts ADD COLUMN IF NOT EXISTS high_price_5d    NUMERIC(12,4);
ALTER TABLE analysis.margin_hype_to_price_forcasts ADD COLUMN IF NOT EXISTS low_price_5d     NUMERIC(12,4);
ALTER TABLE analysis.margin_hype_to_price_forcasts ADD COLUMN IF NOT EXISTS days_to_high_5d  INTEGER;
ALTER TABLE analysis.margin_hype_to_price_forcasts ADD COLUMN IF NOT EXISTS days_to_low_5d   INTEGER;
ALTER TABLE analysis.margin_hype_to_price_forcasts ADD COLUMN IF NOT EXISTS high_price_20d   NUMERIC(12,4);
ALTER TABLE analysis.margin_hype_to_price_forcasts ADD COLUMN IF NOT EXISTS low_price_20d    NUMERIC(12,4);
ALTER TABLE analysis.margin_hype_to_price_forcasts ADD COLUMN IF NOT EXISTS days_to_high_20d INTEGER;
ALTER TABLE analysis.margin_hype_to_price_forcasts ADD COLUMN IF NOT EXISTS days_to_low_20d  INTEGER;
ALTER TABLE analysis.margin_hype_to_price_forcasts ADD COLUMN IF NOT EXISTS high_price_60d   NUMERIC(12,4);
ALTER TABLE analysis.margin_hype_to_price_forcasts ADD COLUMN IF NOT EXISTS low_price_60d    NUMERIC(12,4);
ALTER TABLE analysis.margin_hype_to_price_forcasts ADD COLUMN IF NOT EXISTS days_to_high_60d INTEGER;
ALTER TABLE analysis.margin_hype_to_price_forcasts ADD COLUMN IF NOT EXISTS days_to_low_60d  INTEGER;

-- Migrate sec_type CHECK constraint (safe to re-run).
ALTER TABLE analysis.margin_hype_to_price_forcasts DROP CONSTRAINT IF EXISTS chk_margin_hype_to_price_forcasts_sec_type;
ALTER TABLE analysis.margin_hype_to_price_forcasts
    ADD CONSTRAINT chk_margin_hype_to_price_forcasts_sec_type
        CHECK (sec_type IN ('etf', 'stock', 'index'));

-- Indexes for common access patterns:
--   1. Per-security time series of forcasts (joins with margin_changes
--      on the PK to fetch the full trend + forward performance profile).
--   2. Forward-window analysis (slicing by end_date to find trends that
--      ended on a given date and their subsequent price performance).
CREATE INDEX IF NOT EXISTS idx_margin_hype_to_price_forcasts_sec_type_code
    ON analysis.margin_hype_to_price_forcasts (sec_type, code);
CREATE INDEX IF NOT EXISTS idx_margin_hype_to_price_forcasts_end_date
    ON analysis.margin_hype_to_price_forcasts (end_date);

COMMENT ON TABLE  analysis.margin_hype_to_price_forcasts IS 'Per-(sec_type, code, trend) FORWARD price performance after a margin trend episode ends. One row per margin_changes trend episode (1:1 via FK). For each of 3 forward windows (5, 20, 60 TRADING days after end_date), stores: high_price (MAX close), low_price (MIN close), days_to_high (trading days from window start to high), days_to_low (trading days from window start to low). Validates whether margin UP/DOWN trends have predictive value for future price moves. sec_type ∈ {etf, stock, index}. Built by analyze.margins.changes.forcasts (truncate-then-recompute); all INSERTs in Python per project rule.';
COMMENT ON COLUMN analysis.margin_hype_to_price_forcasts.code             IS 'Security ticker matching the margin_changes FK target.';
COMMENT ON COLUMN analysis.margin_hype_to_price_forcasts.sec_type         IS 'Subject security type: etf, stock, or index. Must match the corresponding margin_changes row.';
COMMENT ON COLUMN analysis.margin_hype_to_price_forcasts.start_date       IS 'Trend episode start date (inclusive). FK to margin_changes PK.';
COMMENT ON COLUMN analysis.margin_hype_to_price_forcasts.end_date         IS 'Trend episode end date (inclusive). Forward windows start from end_date + 1.';
COMMENT ON COLUMN analysis.margin_hype_to_price_forcasts.high_price_5d     IS 'MAX(close) over the 5-TRADING-DAY forward window [end_date+1 .. end_date+5]. NULL when insufficient price data.';
COMMENT ON COLUMN analysis.margin_hype_to_price_forcasts.low_price_5d      IS 'MIN(close) over the 5-TRADING-DAY forward window [end_date+1 .. end_date+5]. NULL when insufficient price data.';
COMMENT ON COLUMN analysis.margin_hype_to_price_forcasts.days_to_high_5d   IS 'Trading days from end_date+1 (window start) to the high_price_5d. 0 = high reached on the first day. NULL when high_price_5d is NULL.';
COMMENT ON COLUMN analysis.margin_hype_to_price_forcasts.days_to_low_5d    IS 'Trading days from end_date+1 (window start) to the low_price_5d. 0 = low reached on the first day. NULL when low_price_5d is NULL.';
COMMENT ON COLUMN analysis.margin_hype_to_price_forcasts.high_price_20d    IS 'MAX(close) over the 20-TRADING-DAY forward window [end_date+1 .. end_date+20]. NULL when insufficient price data.';
COMMENT ON COLUMN analysis.margin_hype_to_price_forcasts.low_price_20d     IS 'MIN(close) over the 20-TRADING-DAY forward window [end_date+1 .. end_date+20]. NULL when insufficient price data.';
COMMENT ON COLUMN analysis.margin_hype_to_price_forcasts.days_to_high_20d  IS 'Trading days from end_date+1 (window start) to the high_price_20d. 0 = high reached on the first day. NULL when high_price_20d is NULL.';
COMMENT ON COLUMN analysis.margin_hype_to_price_forcasts.days_to_low_20d   IS 'Trading days from end_date+1 (window start) to the low_price_20d. 0 = low reached on the first day. NULL when low_price_20d is NULL.';
COMMENT ON COLUMN analysis.margin_hype_to_price_forcasts.high_price_60d    IS 'MAX(close) over the 60-TRADING-DAY forward window [end_date+1 .. end_date+60]. NULL when insufficient price data.';
COMMENT ON COLUMN analysis.margin_hype_to_price_forcasts.low_price_60d     IS 'MIN(close) over the 60-TRADING-DAY forward window [end_date+1 .. end_date+60]. NULL when insufficient price data.';
COMMENT ON COLUMN analysis.margin_hype_to_price_forcasts.days_to_high_60d  IS 'Trading days from end_date+1 (window start) to the high_price_60d. 0 = high reached on the first day. NULL when high_price_60d is NULL.';
COMMENT ON COLUMN analysis.margin_hype_to_price_forcasts.days_to_low_60d   IS 'Trading days from end_date+1 (window start) to the low_price_60d. 0 = low reached on the first day. NULL when low_price_60d is NULL.';

-- ----------------------------------------------------------------------------
--  Register in analysis.analysis_identity
-- ----------------------------------------------------------------------------
INSERT INTO analysis.analysis_identity (name, detail_name, summary_name, last_run_datetime, description) VALUES
    ('margin_hype_to_price_forcasts', 'margin_hype_to_price_forcasts', NULL, NOW(),
     'Per-(sec_type, code, trend) FORWARD price performance after a margin trend episode ends. One row per margin_changes trend episode (1:1 via FK on code, sec_type, start_date, end_date). For each of 3 forward windows (5, 20, 60 TRADING days after end_date): high_price (MAX close), low_price (MIN close), days_to_high (trading days from window start to high), days_to_low (trading days from window start to low). Validates whether margin UP/DOWN trends have predictive value for future price moves. sec_type ∈ {etf, stock, index}. Built by analyze.margins.changes.forcasts (truncate-then-recompute); all INSERTs in Python per project rule.')
ON CONFLICT (name) DO UPDATE SET
    detail_name       = EXCLUDED.detail_name,
    summary_name      = EXCLUDED.summary_name,
    last_run_datetime = NOW(),
    description       = EXCLUDED.description;
