-- ============================================================================
--  Margin Tech Stats — per-(sec_type, code, date) technical indicators on the
--  RONGZI (融资 / cash-borrow) margin flows sourced from stats.*_margin.
--
--  Source tables:
--    stats.etf_liquidity_margin   — ETF liquidity + margin balances (融资融券)
--    stats.stock_liquidity_margin — stock liquidity + margin balances
--  (Indices have NO direct margin data — margin trading is on ETFs / stocks
--  only. The sec_type='index' rows in this table are NOT sourced from a
--  raw margin table; they are the weighted-AVERAGE of constituent stocks'
--  rz_balance aggregated via the analysis.margin_index_series VIEW, with
--  the regime-detection cols (slope/zscore) computed on that aggregated
--  series in Python. See analyze.margins.__main__ step 1b.)
--
--  SCOPE — RONGZI ONLY (融资, cash borrow to buy). RONQIN (融券, sec borrow
--  to short) is INTENTIONALLY EXCLUDED per spec. Only the two rongzi series
--  are tracked:
--    margin_balance ← stats.*_margin.rz_balance
--                     融资余额 — outstanding cash borrowed to buy (a STOCK /
--                     cumulative balance, yuan). Previously total_balance
--                     (rz_balance + rq_balance_amt) but rq_* are sec borrow
--                     and are dropped per the rongzi-only scope.
--    margin_buy     ← stats.*_margin.rz_buy
--                     融资买入额 — daily cash-borrow-to-buy amount (a FLOW,
--                     yuan).
--
--  For EACH of the two series, this table stores:
--    ma5 / ma20 / ma60 — simple moving average over 5 / 20 / 60 trading days
--                        (pandas rolling(W, min_periods=1) per code, so the
--                        first W-1 rows of each code are partial means, NOT
--                        NULL — mirrors stats.etf_tech_stats.ma5 convention
--                        rather than mov_ave_spreads_detail.std_Ndays which
--                        NULLs until the window is full).
--    slope             — fractional day-over-day change
--                        (X[t] - X[t-1]) / X[t-1], matching the convention
--                        used by analysis.mov_ave_spreads_detail.*_slope.
--                        NULL on the first date of each code or when
--                        X[t] / X[t-1] is NULL or X[t-1] <= 0 (denominator
--                        guard — a zero prior balance / flow would otherwise
--                        produce +/-inf).
--
--  UNIVERSE FILTER (Python build script)
--    Only securities with at least one non-zero rz_balance row in the LAST
--    CALENDAR MONTH (~30 days) are materialized. Stale / delisted / suspended
--    securities with no recent rongzi activity are dropped to keep the table
--    focused on actively margin-traded names.
--
--  Table: analysis.margin_tech_stats
--    PK: (sec_type, code, date)
--    sec_type ∈ ('etf' | 'stock' | 'index')  — 'index' rows are aggregated
--    from the margin_index_series VIEW (weighted-avg of constituent stocks).
--
--  POPULATION
--    analyze.margins (Python module, truncate-then-recompute on every run).
--    Per project rule, ALL INSERTs are in Python — no raw INSERT...SELECT SQL
--    in this file.
--
--  Register in analysis.analysis_identity (name='margin_tech_stats').
-- ============================================================================
CREATE TABLE IF NOT EXISTS analysis.margin_tech_stats (
    sec_type        TEXT         NOT NULL,  -- 'etf' | 'stock' | 'index' ('index' rows aggregated from margin_index_series VIEW — no raw margin table)
    code            TEXT         NOT NULL,
    date            DATE         NOT NULL,

    -- margin_balance = stats.*_margin.rz_balance (yuan, STOCK).
    -- 5/20/60-day SMA of rongzi outstanding balance.
    margin_balance_ma5    NUMERIC(24,4),
    margin_balance_ma20   NUMERIC(24,4),
    margin_balance_ma60   NUMERIC(24,4),


    -- margin_buy = stats.*_margin.rz_buy (yuan, FLOW).
    -- 5/20/60-day SMA of daily rongzi buy amount.
    margin_buy_ma5        NUMERIC(24,4),
    margin_buy_ma20       NUMERIC(24,4),
    margin_buy_ma60       NUMERIC(24,4),

    -- Fractional day-over-day change of rz_balance/buy.
    -- NUMERIC(18,6) — observed max |slope| ~16K (tiny-denominator edge
    -- cases); NUMERIC(10,6) (max 10^4) overflows on real data.
    margin_balance_slope  NUMERIC(18,6),
    margin_buy_slope      NUMERIC(18,6),

    -- ---- Regime-detection cols (for margin_changes trend detection) ----
    -- Slope MA / std / z-score over a 20-trading-day window, computed
    -- per (sec_type, code) on the daily slope. The z-score measures how
    -- anomalous today's slope is vs the recent 20d mean — used by the
    -- margin_changes step to filter SIGNIFICANT trends (UP trends kept
    -- when zscore > 0 for all days; DOWN trends kept when zscore < 0).
    -- slope_ma5 smooths 1-day noise; slope_ma20 is the medium-term
    -- trend; slope_ma255 is the long-term (~1 trading year) trend;
    -- slope_std20 is the rolling std (ddof=1, sample std);
    -- zscore_20d = (slope - slope_ma20) / slope_std20, NULL when
    -- slope_std20 <= 0 (flat / no variance).
    margin_balance_slope_ma5        NUMERIC(18,6),
    margin_balance_slope_ma20       NUMERIC(18,6),
    margin_balance_slope_std20      NUMERIC(18,6),
    margin_balance_slope_zscore_20d NUMERIC(18,6),

    margin_buy_slope_ma5        NUMERIC(18,6),
    margin_buy_slope_ma20       NUMERIC(18,6),
    margin_buy_slope_std20      NUMERIC(18,6),
    margin_buy_slope_zscore_20d NUMERIC(18,6),

    margin_balance_slope_ma255      NUMERIC(18,6),

    CONSTRAINT pk_margin_tech_stats PRIMARY KEY (sec_type, code, date),
    CONSTRAINT chk_margin_tech_stats_sec_type
        CHECK (sec_type IN ('etf', 'stock', 'index'))
);

-- Idempotent migration: CREATE TABLE IF NOT EXISTS does not retro-fit columns
-- to an already-existing table, so ADD COLUMN IF NOT EXISTS is required for
-- production upgrades. No-op on fresh installs. The legacy margin_sell_*
-- columns (which sourced from rq_sell_qty — sec borrow) are DROPPED because
-- the spec restricts this table to rongzi (融资) only.
ALTER TABLE analysis.margin_tech_stats ADD COLUMN IF NOT EXISTS margin_balance_ma5    NUMERIC(24,4);
ALTER TABLE analysis.margin_tech_stats ADD COLUMN IF NOT EXISTS margin_balance_ma20   NUMERIC(24,4);
ALTER TABLE analysis.margin_tech_stats ADD COLUMN IF NOT EXISTS margin_balance_ma60   NUMERIC(24,4);
ALTER TABLE analysis.margin_tech_stats ADD COLUMN IF NOT EXISTS margin_balance_slope  NUMERIC(18,6);
ALTER TABLE analysis.margin_tech_stats ADD COLUMN IF NOT EXISTS margin_buy_ma5        NUMERIC(24,4);
ALTER TABLE analysis.margin_tech_stats ADD COLUMN IF NOT EXISTS margin_buy_ma20       NUMERIC(24,4);
ALTER TABLE analysis.margin_tech_stats ADD COLUMN IF NOT EXISTS margin_buy_ma60       NUMERIC(24,4);
ALTER TABLE analysis.margin_tech_stats ADD COLUMN IF NOT EXISTS margin_buy_slope      NUMERIC(18,6);
-- Regime-detection cols (for margin_changes). slope_ma5/ma20/ma255 = rolling
-- mean of the daily slope per (sec_type, code); slope_std20 = rolling sample
-- std (ddof=1); zscore_20d = (slope - slope_ma20) / slope_std20, NULL when
-- std <= 0. All use min_periods=1 (partial values for first W-1 rows).
ALTER TABLE analysis.margin_tech_stats ADD COLUMN IF NOT EXISTS margin_balance_slope_ma5        NUMERIC(18,6);
ALTER TABLE analysis.margin_tech_stats ADD COLUMN IF NOT EXISTS margin_balance_slope_ma20       NUMERIC(18,6);
ALTER TABLE analysis.margin_tech_stats ADD COLUMN IF NOT EXISTS margin_balance_slope_std20      NUMERIC(18,6);
ALTER TABLE analysis.margin_tech_stats ADD COLUMN IF NOT EXISTS margin_balance_slope_zscore_20d NUMERIC(18,6);
ALTER TABLE analysis.margin_tech_stats ADD COLUMN IF NOT EXISTS margin_buy_slope_ma5        NUMERIC(18,6);
ALTER TABLE analysis.margin_tech_stats ADD COLUMN IF NOT EXISTS margin_buy_slope_ma20       NUMERIC(18,6);
ALTER TABLE analysis.margin_tech_stats ADD COLUMN IF NOT EXISTS margin_buy_slope_std20      NUMERIC(18,6);
ALTER TABLE analysis.margin_tech_stats ADD COLUMN IF NOT EXISTS margin_buy_slope_zscore_20d NUMERIC(18,6);
ALTER TABLE analysis.margin_tech_stats ADD COLUMN IF NOT EXISTS margin_balance_slope_ma255      NUMERIC(18,6);
-- Migrate the sec_type CHECK constraint: the original CREATE TABLE
-- (pre-'index' support) allowed only ('etf', 'stock'). CREATE TABLE IF
-- NOT EXISTS does NOT retro-fit the CHECK on an existing table, so
-- drop + re-add to permit 'index' rows (aggregated from the
-- margin_index_series VIEW). Safe to re-run.
ALTER TABLE analysis.margin_tech_stats DROP CONSTRAINT IF EXISTS chk_margin_tech_stats_sec_type;
ALTER TABLE analysis.margin_tech_stats
    ADD CONSTRAINT chk_margin_tech_stats_sec_type
        CHECK (sec_type IN ('etf', 'stock', 'index'));
-- Drop legacy sec-borrow cols (rongzi-only scope). Order matters: slope_ma5
-- variants of margin_sell_* are dropped alongside the others.
ALTER TABLE analysis.margin_tech_stats DROP COLUMN IF EXISTS margin_sell_ma5;
ALTER TABLE analysis.margin_tech_stats DROP COLUMN IF EXISTS margin_sell_ma20;
ALTER TABLE analysis.margin_tech_stats DROP COLUMN IF EXISTS margin_sell_ma60;
ALTER TABLE analysis.margin_tech_stats DROP COLUMN IF EXISTS margin_sell_slope;
ALTER TABLE analysis.margin_tech_stats DROP COLUMN IF EXISTS margin_sell_slope_ma5;

-- Indexes for the common access patterns:
--   1. Per-security time series (drives per-code margin trend charts).
--   2. Per-date snapshot (drives the latest-date cross-sectional view).
--   3. sec_type-scoped scan (incremental upsert by sec_type).
-- The PK already covers (sec_type, code, date) equality + range scans, so no
-- duplicate index on that prefix (mirrors mov_ave_spreads_detail convention).
CREATE INDEX IF NOT EXISTS idx_margin_tech_stats_code_sec_type_date
    ON analysis.margin_tech_stats (code, sec_type, date);
CREATE INDEX IF NOT EXISTS idx_margin_tech_stats_date
    ON analysis.margin_tech_stats (date);
CREATE INDEX IF NOT EXISTS idx_margin_tech_stats_sec_type_date
    ON analysis.margin_tech_stats (sec_type, date);

COMMENT ON TABLE  analysis.margin_tech_stats                  IS 'Per-(sec_type, code, date) technical indicators on RONGZI (融资 / cash-borrow) margin flows. sec_type ∈ {etf, stock, index}. ''etf''/''stock'' sourced from stats.etf_liquidity_margin / stats.stock_liquidity_margin. ''index'' rows are NOT from a raw margin table — they are the weighted-AVERAGE of constituent stocks'' rz_balance aggregated via the analysis.margin_index_series VIEW, with regime-detection cols computed on that aggregated series in Python (aggregate-then-compute — slope is a ratio / non-additive). RONQIN (融券 / sec borrow) is EXCLUDED — only rongzi is tracked. Two series: margin_balance (rz_balance, yuan, STOCK), margin_buy (rz_buy, yuan, FLOW). For each: ma5/ma20/ma60 (pandas rolling(W, min_periods=1) per code — partial mean for first W-1 rows, NOT NULL), slope ((X[t]-X[t-1])/X[t-1], NULL on first date or X[t-1] <= 0), and regime-detection cols (slope_ma5/ma20 = rolling mean of slope, slope_ma255 = 255d rolling mean, slope_std20 = rolling sample std, zscore_20d = (slope-slope_ma20)/slope_std20 NULL when std<=0) consumed by analysis.margin_changes for UP/DOWN trend classification. Universe filter (etf/stock only): securities with at least one non-zero rz_balance row in the last calendar month are materialized. Built by analyze.margins (truncate-then-recompute); all INSERTs in Python per project rule.';
COMMENT ON COLUMN analysis.margin_tech_stats.sec_type         IS 'Subject security type: etf, stock, or index. ''etf''/''stock'' are sourced from the raw margin tables. ''index'' rows are aggregated from the analysis.margin_index_series VIEW (weighted-avg of constituent stocks'' rz_balance by parent_index_weight); the regime-detection cols are computed on that aggregated series in Python.';
COMMENT ON COLUMN analysis.margin_tech_stats.code             IS 'Security ticker with exchange suffix, e.g. "159001.SZ" (ETF) or "600008.SS" (stock).';
COMMENT ON COLUMN analysis.margin_tech_stats.date             IS 'Trading date.';
COMMENT ON COLUMN analysis.margin_tech_stats.margin_balance_ma5    IS '5-trading-day SMA of stats.*_margin.rz_balance (yuan) per (sec_type, code). pandas rolling(5, min_periods=1) — partial mean for the first 4 rows of each code, NOT NULL. rz_balance = outstanding cash borrowed to buy (融资余额, a cumulative STOCK).';
COMMENT ON COLUMN analysis.margin_tech_stats.margin_balance_ma20   IS '20-trading-day SMA of rz_balance (yuan). pandas rolling(20, min_periods=1) — partial mean for the first 19 rows, NOT NULL.';
COMMENT ON COLUMN analysis.margin_tech_stats.margin_balance_ma60   IS '60-trading-day SMA of rz_balance (yuan). pandas rolling(60, min_periods=1) — partial mean for the first 59 rows, NOT NULL.';
COMMENT ON COLUMN analysis.margin_tech_stats.margin_balance_slope  IS 'Fractional day-over-day change of rz_balance: (X[t] - X[t-1]) / X[t-1]. Signed ratio (e.g. 0.02 = +2% day-over-day). NULL on the first date of each code or when X[t] / X[t-1] is NULL or X[t-1] <= 0 (denominator guard). NUMERIC(18,6) — observed max |slope| ~16K from tiny-denominator edge cases (most values are small; balance changes gradually). Convention matches analysis.mov_ave_spreads_detail.*_slope.';
COMMENT ON COLUMN analysis.margin_tech_stats.margin_buy_ma5        IS '5-trading-day SMA of stats.*_margin.rz_buy (yuan, FLOW — daily rongzi BUY amount / 融资买入额). pandas rolling(5, min_periods=1) — partial mean for the first 4 rows, NOT NULL.';
COMMENT ON COLUMN analysis.margin_tech_stats.margin_buy_ma20       IS '20-trading-day SMA of rz_buy (yuan). pandas rolling(20, min_periods=1) — partial mean for the first 19 rows, NOT NULL.';
COMMENT ON COLUMN analysis.margin_tech_stats.margin_buy_ma60       IS '60-trading-day SMA of rz_buy (yuan). pandas rolling(60, min_periods=1) — partial mean for the first 59 rows, NOT NULL.';
COMMENT ON COLUMN analysis.margin_tech_stats.margin_buy_slope      IS 'Fractional day-over-day change of rz_buy: (X[t] - X[t-1]) / X[t-1]. Signed ratio. NULL on the first date of each code or when X[t] / X[t-1] is NULL or X[t-1] <= 0. NUMERIC(18,6) — observed max |slope| ~486K because rz_buy is a FLOW that can swing from near-zero to large values day-to-day (NUMERIC(10,6) overflowed on real data).';
COMMENT ON COLUMN analysis.margin_tech_stats.margin_balance_slope_ma5        IS '5-trading-day SMA of margin_balance_slope per (sec_type, code) — smooths 1-day noise on the balance slope. pandas rolling(5, min_periods=1) — partial mean for the first 4 rows of each code. Used by margin_changes for UP/DOWN trend classification (sign of slope_ma5 = direction of the balance move).';
COMMENT ON COLUMN analysis.margin_tech_stats.margin_balance_slope_ma20       IS '20-trading-day SMA of margin_balance_slope per (sec_type, code) — the medium-term trend of the balance slope. pandas rolling(20, min_periods=1) — partial mean for the first 19 rows. Used as the baseline for z-score significance (how anomalous is the current slope vs the 20d mean).';
COMMENT ON COLUMN analysis.margin_tech_stats.margin_balance_slope_std20      IS '20-trading-day rolling SAMPLE std (ddof=1) of margin_balance_slope per (sec_type, code). pandas rolling(20, min_periods=1). Measures the volatility of the balance slope over the recent 20d — used as the denominator of the z-score. NULL/NaN for the first row of each code (no prior values).';
COMMENT ON COLUMN analysis.margin_tech_stats.margin_balance_slope_zscore_20d IS 'Z-score of margin_balance_slope vs its 20d window: (slope - slope_ma20) / slope_std20. NULL when slope_std20 is NULL or <= 0 (flat / no variance — the security has near-constant balance slope, so significance is undefined). Range typically [-4, +4]; |z| > 1.5 = significantly anomalous. Used by margin_changes to filter SIGNIFICANT trends (UP kept when zscore > 0 for all trend days; DOWN kept when zscore < 0 for all trend days).';
COMMENT ON COLUMN analysis.margin_tech_stats.margin_buy_slope_ma5        IS '5-trading-day SMA of margin_buy_slope per (sec_type, code) — smooths 1-day noise on the buy-flow slope. pandas rolling(5, min_periods=1). Secondary regime signal (balance is primary).';
COMMENT ON COLUMN analysis.margin_tech_stats.margin_buy_slope_ma20       IS '20-trading-day SMA of margin_buy_slope per (sec_type, code) — medium-term trend of the buy-flow slope. pandas rolling(20, min_periods=1). Baseline for the buy-flow z-score.';
COMMENT ON COLUMN analysis.margin_tech_stats.margin_buy_slope_std20      IS '20-trading-day rolling SAMPLE std (ddof=1) of margin_buy_slope per (sec_type, code). pandas rolling(20, min_periods=1).';
COMMENT ON COLUMN analysis.margin_tech_stats.margin_buy_slope_zscore_20d IS 'Z-score of margin_buy_slope vs its 20d window: (slope - slope_ma20) / slope_std20. NULL when slope_std20 is NULL or <= 0. Secondary regime signal (balance z-score is the primary).';
COMMENT ON COLUMN analysis.margin_tech_stats.margin_balance_slope_ma255      IS '255-trading-day SMA of margin_balance_slope per (sec_type, code) — the long-term trend of the balance slope (~1 trading year). pandas rolling(255, min_periods=1) — partial mean for the first 254 rows. Used as a long-term baseline for trend significance filtering in analysis.margin_changes (UP trends kept when slope > ma255; DOWN trends kept when slope < ma255).';

-- ----------------------------------------------------------------------------
--  Register in analysis.analysis_identity
-- ----------------------------------------------------------------------------
INSERT INTO analysis.analysis_identity (name, detail_name, summary_name, last_run_datetime, description) VALUES
    ('margin_tech_stats', 'margin_tech_stats', NULL, NOW(),
     'Per-(sec_type, code, date) technical indicators on RONGZI (融资 / cash-borrow) margin flows. sec_type ∈ {etf, stock, index}. ''etf''/''stock'' sourced from stats.etf_liquidity_margin / stats.stock_liquidity_margin. ''index'' rows aggregated from analysis.margin_index_series VIEW (weighted-avg of constituent stocks'' rz_balance); regime-detection cols computed on the aggregated series in Python. RONQIN (融券 / sec borrow) EXCLUDED — rongzi only. Two series: margin_balance (rz_balance, yuan, STOCK), margin_buy (rz_buy, yuan, FLOW). For each: ma5/ma20/ma60 (pandas rolling(W, min_periods=1) per code — partial mean for first W-1 rows, NOT NULL), slope ((X[t]-X[t-1])/X[t-1], NULL on first date or X[t-1] <= 0), and regime-detection cols (slope_ma5/ma20 = rolling mean of slope, slope_ma255 = 255d rolling mean, slope_std20 = rolling sample std ddof=1, zscore_20d = (slope-slope_ma20)/slope_std20 NULL when std<=0) consumed by analysis.margin_changes for UP/DOWN trend classification. Universe filter (etf/stock only): securities with non-zero rz_balance in last calendar month. Built by analyze.margins (truncate-then-recompute); all INSERTs in Python per project rule.')
ON CONFLICT (name) DO UPDATE SET
    detail_name       = EXCLUDED.detail_name,
    summary_name      = EXCLUDED.summary_name,
    last_run_datetime = NOW(),
    description       = EXCLUDED.description;


-- ============================================================================
--  Margin Industry Stats — per-(date, industry_id) SUM aggregation of stock
--  AND ETF RONGZI (融资 / cash-borrow) margin flows.
--
--  Source tables:
--    stats.stock_liquidity_margin — stock liquidity + margin balances (融资融券)
--    stats.etf_liquidity_margin   — ETF liquidity + margin balances
--
--  SCOPE — RONGZI ONLY (融资, cash borrow to buy). RONQIN (融券, sec borrow
--  to short) is INTENTIONALLY EXCLUDED per spec.
--
--  INDUSTRY MAPPING (how securities are assigned to industries):
--    Stock → industry_id via stats.sec_classification WHERE type='stock'
--            AND parent_index_is_primary=TRUE AND parent_index_code <> ''.
--            Each stock has exactly ONE primary row (the row with
--            MAX(parent_index_weight)); its industry_id is the stock's
--            primary industry. Stocks with no qualifying parent index
--            (parent_index_code='') are excluded — they have no industry.
--            NOTE: BROAD sector indices are already excluded from stock
--            rows by build_classification (stock rows only reference
--            industry indices, weight > 2%).
--
--    ETF  → industry_id via TWO-HOP JOIN (mirrors stats.etf_trading_amt
--            in 08_index_exts.sql):
--              etf.parent_index_code → index.code → index.industry_id
--            This is the proven pattern in the codebase. Each ETF has
--            exactly ONE parent_index_code, so each ETF maps to exactly
--            one industry.
--
--  AGGREGATION OPERATOR: SUM
--    For each (date, industry_id):
--      stock_margin_balance = SUM(stock_liquidity_margin.rz_balance)
--                              across stocks whose primary industry = industry_id
--      etf_margin_balance   = SUM(etf_liquidity_margin.rz_balance)
--                              across ETFs tracking indices in industry_id
--      (similarly for margin_buy = rz_buy)
--
--    Stock and ETF components are stored SEPARATELY (not pre-summed) so
--    that analysis.margin_industry_correlation can correlate the stock
--    margin series against the ETF margin series within each industry.
--    The total (stock + etf) is materialized via GENERATED columns.
--
--  COUNT COLUMNS — diagnostic for the universe filter
--    stock_count           = number of stocks in this industry with a row
--                            on this date (includes 0-margin rows)
--    stock_margin_count    = number of those stocks with NON-ZERO rz_balance
--                            on this date (i.e. actively rongzi-traded)
--    stock_margin_count_share = stock_margin_count / stock_count (NULL when
--                            stock_count = 0 via NULLIF guard; the GENERATED
--                            expression uses NULLIF to avoid div-by-zero)
--    stock_margin_weight_share = SUM of parent_index_weight across the
--                            actively-rongzi-traded stocks (0..1 range when
--                            weights are normalized within industry).
--                            Populated by Python; not GENERATED because it
--                            depends on the parent-index weight from
--                            sec_classification, not just counts.
--    (etf_count / etf_margin_count / etf_margin_count_share mirror these)
--    These columns expose how many securities per industry are filtered out
--    by the "active rongzi" requirement on each date.
--
--  UNIVERSE FILTER (Python build script)
--    Only securities with at least one non-zero rz_balance row in the LAST
--    CALENDAR MONTH are aggregated. Stale / delisted / suspended securities
--    are dropped entirely; their rows never reach this table.
--
--  UNITS
--    margin_balance, margin_buy: yuan (both stock and ETF sources are yuan)
--
--  Table: analysis.margin_industry_stats
--    PK: (date, industry_id)
--
--  POPULATION
--    analyze.margins (Python module, truncate-then-recompute on every run).
--    Per project rule, ALL INSERTs are in Python — no raw INSERT...SELECT SQL
--    in this file.
--
--  Register in analysis.analysis_identity (name='margin_industry_stats').
-- ============================================================================
CREATE TABLE IF NOT EXISTS analysis.margin_industry_stats (
    date                      DATE          NOT NULL,
    industry_id               TEXT          NOT NULL,

    -- Display label (denormalized, from stats.sec_classification)
    industry_label            TEXT          NOT NULL DEFAULT '',

    -- Counts of securities contributing on this date.
    -- stock_count / etf_count = total securities in industry on this date
    -- (includes 0-margin rows). *_margin_count = subset with non-zero
    -- rz_balance on this date (actively rongzi-traded). *_margin_count_share
    -- = ratio; NULL when EITHER operand is NULL or 0 (NULLIF on both —
    -- avoids div-by-zero AND suppresses misleading 0.00 when numerator is 0).
    -- *_margin_weight_share = SUM of parent_index_weight across the
    -- actively-rongzi-traded subset (NOT GENERATED — populated by Python).
    stock_count               INTEGER,
    stock_margin_count        INTEGER,
    stock_margin_count_share  NUMERIC(8,4) GENERATED ALWAYS AS
        (NULLIF(stock_margin_count, 0)::NUMERIC / NULLIF(stock_count, 0)) STORED,
    stock_margin_weight_share NUMERIC(8,4),

    etf_count                 INTEGER,
    etf_margin_count          INTEGER,
    etf_margin_count_share    NUMERIC(8,4) GENERATED ALWAYS AS
        (NULLIF(etf_margin_count, 0)::NUMERIC / NULLIF(etf_count, 0)) STORED,

    -- margin_balance = SUM(rz_balance) — rongzi OUTSTANDING (yuan, STOCK).
    stock_margin_balance      NUMERIC(24,4),
    etf_margin_balance        NUMERIC(24,4),
    total_margin_balance      NUMERIC(24,4) GENERATED ALWAYS AS (stock_margin_balance + etf_margin_balance) STORED,

    -- margin_buy = SUM(rz_buy) — daily rongzi BUY amount / 融资买入额 (yuan, FLOW).
    stock_margin_buy          NUMERIC(24,4),
    etf_margin_buy            NUMERIC(24,4),
    total_margin_buy          NUMERIC(24,4) GENERATED ALWAYS AS (stock_margin_buy + etf_margin_buy) STORED,

    CONSTRAINT pk_margin_industry_stats PRIMARY KEY (date, industry_id)
);

-- Idempotent migration: ADD COLUMN IF NOT EXISTS retro-fits columns to
-- pre-existing installs. No-op on fresh installs. The legacy *_margin_sell
-- and total_margin_sell columns (which sourced from rq_sell_qty — sec borrow)
-- are DROPPED because the spec restricts this table to rongzi (融资) only.
ALTER TABLE analysis.margin_industry_stats ADD COLUMN IF NOT EXISTS industry_label       TEXT         NOT NULL DEFAULT '';
ALTER TABLE analysis.margin_industry_stats ADD COLUMN IF NOT EXISTS stock_count          INTEGER;
ALTER TABLE analysis.margin_industry_stats ADD COLUMN IF NOT EXISTS etf_count            INTEGER;
ALTER TABLE analysis.margin_industry_stats ADD COLUMN IF NOT EXISTS stock_margin_count   INTEGER;
ALTER TABLE analysis.margin_industry_stats ADD COLUMN IF NOT EXISTS etf_margin_count     INTEGER;
ALTER TABLE analysis.margin_industry_stats ADD COLUMN IF NOT EXISTS stock_margin_weight_share NUMERIC(8,4);
ALTER TABLE analysis.margin_industry_stats ADD COLUMN IF NOT EXISTS stock_margin_balance NUMERIC(24,4);
ALTER TABLE analysis.margin_industry_stats ADD COLUMN IF NOT EXISTS etf_margin_balance   NUMERIC(24,4);
ALTER TABLE analysis.margin_industry_stats ADD COLUMN IF NOT EXISTS stock_margin_buy     NUMERIC(24,4);
ALTER TABLE analysis.margin_industry_stats ADD COLUMN IF NOT EXISTS etf_margin_buy       NUMERIC(24,4);
-- Generated total + share columns. GENERATED ALWAYS AS ... STORED so they
-- are maintained by the DB and cannot be written directly. NULL when either
-- source is NULL (build script should COALESCE to 0 when inserting) or when
-- the divisor is 0 (NULLIF guard on the share columns).
-- Share columns use NULLIF on BOTH operands: NULL when numerator OR
-- denominator is NULL/0. DROP+ADD because PostgreSQL cannot ALTER a
-- generated column's expression in place (no SET EXPRESSION).
ALTER TABLE analysis.margin_industry_stats DROP COLUMN IF EXISTS stock_margin_count_share;
ALTER TABLE analysis.margin_industry_stats ADD COLUMN stock_margin_count_share NUMERIC(8,4) GENERATED ALWAYS AS (NULLIF(stock_margin_count, 0)::NUMERIC / NULLIF(stock_count, 0)) STORED;
ALTER TABLE analysis.margin_industry_stats DROP COLUMN IF EXISTS etf_margin_count_share;
ALTER TABLE analysis.margin_industry_stats ADD COLUMN etf_margin_count_share   NUMERIC(8,4) GENERATED ALWAYS AS (NULLIF(etf_margin_count, 0)::NUMERIC / NULLIF(etf_count, 0)) STORED;
ALTER TABLE analysis.margin_industry_stats ADD COLUMN IF NOT EXISTS total_margin_balance NUMERIC(24,4) GENERATED ALWAYS AS (stock_margin_balance + etf_margin_balance) STORED;
ALTER TABLE analysis.margin_industry_stats ADD COLUMN IF NOT EXISTS total_margin_buy     NUMERIC(24,4) GENERATED ALWAYS AS (stock_margin_buy + etf_margin_buy) STORED;
-- Drop legacy sec-borrow columns (rongzi-only scope).
-- Order matters: total_margin_sell (GENERATED from stock + etf) must be
-- dropped BEFORE its dependencies, otherwise PostgreSQL refuses with
-- "cannot drop column ... because other objects depend on it".
ALTER TABLE analysis.margin_industry_stats DROP COLUMN IF EXISTS total_margin_sell;
ALTER TABLE analysis.margin_industry_stats DROP COLUMN IF EXISTS stock_margin_sell;
ALTER TABLE analysis.margin_industry_stats DROP COLUMN IF EXISTS etf_margin_sell;

-- Indexes:
--   1. Per-industry time series (drives per-industry margin charts).
--   2. Per-date snapshot (drives the latest-date cross-sectional view).
CREATE INDEX IF NOT EXISTS idx_margin_industry_stats_industry_date
    ON analysis.margin_industry_stats (industry_id, date);
CREATE INDEX IF NOT EXISTS idx_margin_industry_stats_date
    ON analysis.margin_industry_stats (date);

COMMENT ON TABLE  analysis.margin_industry_stats                  IS 'Per-(date, industry_id) SUM aggregation of stock AND ETF RONGZI (融资) margin flows. Stock→industry via sec_classification(type=stock, parent_index_is_primary=TRUE, parent_index_code<>empty). ETF→industry via two-hop: etf.parent_index_code→index.code→index.industry_id (mirrors stats.etf_trading_amt pattern). RONQIN (融券 / sec borrow) EXCLUDED. Stock and ETF components stored SEPARATELY; total_margin_* columns are GENERATED ALWAYS AS (stock + etf) STORED. *_margin_count + *_margin_count_share columns expose how many securities per industry are actively rongzi-traded (non-zero rz_balance) vs the full industry universe. Universe filter: only securities with non-zero rz_balance in last calendar month. margin_balance=SUM(rz_balance, yuan, STOCK), margin_buy=SUM(rz_buy, yuan, FLOW). Built by analyze.margins (truncate-then-recompute); all INSERTs in Python per project rule.';
COMMENT ON COLUMN analysis.margin_industry_stats.date             IS 'Trading date.';
COMMENT ON COLUMN analysis.margin_industry_stats.industry_id      IS 'Industry identifier (e.g. BANKS, SEMI, BROAD_CSI). Maps from the primary parent index of each stock / the tracking index of each ETF via stats.sec_classification.';
COMMENT ON COLUMN analysis.margin_industry_stats.industry_label   IS 'Display label (denormalized from stats.sec_classification.industry_label).';
COMMENT ON COLUMN analysis.margin_industry_stats.stock_count      IS 'Number of stocks in this industry with a row in stats.stock_liquidity_margin on this date (includes stocks with rz_balance = 0).';
COMMENT ON COLUMN analysis.margin_industry_stats.stock_margin_count      IS 'Number of stocks in this industry with NON-ZERO rz_balance on this date (i.e. actively rongzi-traded). The subset of stock_count with margin activity. Exposes how many stocks per industry pass the active-rongzi filter on each date.';
COMMENT ON COLUMN analysis.margin_industry_stats.stock_margin_count_share IS 'GENERATED ALWAYS AS (NULLIF(stock_margin_count,0) / NULLIF(stock_count,0)) STORED. Ratio of actively-rongzi-traded stocks to total stocks in this industry on this date. Range 0..1. NULL when EITHER operand is NULL or 0 (industry has no stocks, or no actively-traded stocks, on this date). Diagnostic for the universe filter — a low share means most stocks in this industry have no rongzi activity on this date.';
COMMENT ON COLUMN analysis.margin_industry_stats.stock_margin_weight_share IS 'SUM of parent_index_weight across actively-rongzi-traded stocks in this industry on this date. Range 0..1 when weights are normalized within industry. Populated by Python (NOT GENERATED — depends on the per-stock parent-index weight from sec_classification, not just counts). Diagnostic: a high weight-share means the industry''s rongzi activity is concentrated in its heavyweight constituent stocks.';
COMMENT ON COLUMN analysis.margin_industry_stats.etf_count        IS 'Number of ETFs in this industry with a row in stats.etf_liquidity_margin on this date (includes ETFs with rz_balance = 0).';
COMMENT ON COLUMN analysis.margin_industry_stats.etf_margin_count        IS 'Number of ETFs in this industry with NON-ZERO rz_balance on this date (actively rongzi-traded). Subset of etf_count.';
COMMENT ON COLUMN analysis.margin_industry_stats.etf_margin_count_share  IS 'GENERATED ALWAYS AS (NULLIF(etf_margin_count,0) / NULLIF(etf_count,0)) STORED. Ratio of actively-rongzi-traded ETFs to total ETFs in this industry on this date. NULL when EITHER operand is NULL or 0.';
COMMENT ON COLUMN analysis.margin_industry_stats.stock_margin_balance IS 'SUM(stats.stock_liquidity_margin.rz_balance) across stocks whose primary industry = industry_id on this date. Yuan. rz_balance = outstanding cash borrowed to buy (融资余额, a cumulative STOCK). 0 (not NULL) when all stocks have 0 rongzi activity.';
COMMENT ON COLUMN analysis.margin_industry_stats.etf_margin_balance   IS 'SUM(stats.etf_liquidity_margin.rz_balance) across ETFs tracking indices in industry_id on this date. Yuan. 0 (not NULL) when all ETFs have 0 rongzi activity.';
COMMENT ON COLUMN analysis.margin_industry_stats.total_margin_balance IS 'GENERATED ALWAYS AS (stock_margin_balance + etf_margin_balance) STORED. Total rongzi outstanding (yuan) across stocks AND ETFs in this industry on this date. NULL when either source is NULL (build script should COALESCE to 0 when inserting). Cannot be written directly — maintained by the DB.';
COMMENT ON COLUMN analysis.margin_industry_stats.stock_margin_buy     IS 'SUM(stats.stock_liquidity_margin.rz_buy) across stocks whose primary industry = industry_id on this date. Yuan (FLOW — daily rongzi BUY amount / 融资买入额). 0 when no rongzi buy activity.';
COMMENT ON COLUMN analysis.margin_industry_stats.etf_margin_buy       IS 'SUM(stats.etf_liquidity_margin.rz_buy) across ETFs tracking indices in industry_id on this date. Yuan (FLOW). 0 when no rongzi buy activity.';
COMMENT ON COLUMN analysis.margin_industry_stats.total_margin_buy     IS 'GENERATED ALWAYS AS (stock_margin_buy + etf_margin_buy) STORED. Total daily rongzi BUY amount (yuan, FLOW) across stocks AND ETFs in this industry on this date. NULL when either source is NULL. Cannot be written directly — maintained by the DB.';

-- ----------------------------------------------------------------------------
--  Register in analysis.analysis_identity
-- ----------------------------------------------------------------------------
INSERT INTO analysis.analysis_identity (name, detail_name, summary_name, last_run_datetime, description) VALUES
    ('margin_industry_stats', 'margin_industry_stats', NULL, NOW(),
     'Per-(date, industry_id) SUM aggregation of stock AND ETF RONGZI (融资) margin flows. Stock→industry via sec_classification(type=stock, parent_index_is_primary=TRUE, parent_index_code<>empty). ETF→industry via two-hop: etf.parent_index_code→index.code→index.industry_id. RONQIN (融券) EXCLUDED. Stock and ETF components stored SEPARATELY; total_margin_* columns GENERATED ALWAYS AS (stock + etf) STORED. *_margin_count + *_margin_count_share + *_margin_weight_share expose active-rongzi ratio per industry. margin_balance=SUM(rz_balance, yuan), margin_buy=SUM(rz_buy, yuan). Universe filter: only securities with non-zero rz_balance in last calendar month. Built by analyze.margins (truncate-then-recompute); all INSERTs in Python per project rule.')
ON CONFLICT (name) DO UPDATE SET
    detail_name       = EXCLUDED.detail_name,
    summary_name      = EXCLUDED.summary_name,
    last_run_datetime = NOW(),
    description       = EXCLUDED.description;


-- ============================================================================
--  Margin Industry Correlation — pairwise rolling Pearson correlation of
--  RONGZI (融资) margin flows/balances between two SECURITIES within the
--  SAME industry, over 5 / 20 / 60 / 120 / 255 trading-day windows, for
--  each attribution_type ∈ ('index', 'etf', 'total').
--
--  SCOPE — RONGZI ONLY (融资, cash borrow to buy). RONQIN (融券, sec borrow
--  to short) is INTENTIONALLY EXCLUDED per spec, so only two series
--  (margin_balance, margin_buy) are correlated.
--
--  PURPOSE
--    "Within one industry, do two securities' rongzi flows move together?"
--    A high positive correlation means the two securities' rongzi activity
--    moves in lockstep (shared underlying exposure — e.g. two ETFs tracking
--    the same index, or an ETF vs its underlying index). A low/negative
--    correlation suggests divergence (one security's rongzi rising while
--    the other's falls — different positioning / hedging).
--
--  PAIR SEMANTICS (security_code ↔ benchmark_code, both in industry_id)
--    A "security" in a pair is either an ETF code (e.g. 510050.SS) or an
--    index code (e.g. 000300). ALL pairs within an industry are
--    materialized: index↔index, index↔etf, and etf↔etf. industry_id is
--    the shared industry of both securities — an ETF inherits its tracking
--    index's industry; an index carries its own industry_id.
--
--  ATTRIBUTION_TYPE (which margin series is correlated for each security)
--    'index' — the security's INDEX margin series = weighted-average of its
--              constituent stocks' rongzi:
--                SUM(stock_rz × parent_index_weight) / SUM(parent_index_weight)
--              For an ETF: its TRACKING index's weighted-stock series.
--              For an index: its OWN weighted-stock series.
--              Provided by the analysis.margin_index_series VIEW.
--    'etf'   — the security's own ETF margin series (rz_balance / rz_buy
--              from stats.etf_liquidity_margin). NULL for index securities,
--              so index↔index pairs under 'etf' are NOT materialized.
--
--  SOURCE
--    analysis.margin_index_series (VIEW — per (index_code, date) weighted-
--    average rongzi from stock_liquidity_margin × sec_classification
--    parent_index_weight): the 'index' series.
--    stats.etf_liquidity_margin: the 'etf' series.
--
--  CONVENTION (mirrors analysis.industry_correlations exactly)
--    For each pair of securities (A, B) within an industry and each
--    attribution_type, compute the rolling W-day Pearson correlation
--    between A's rongzi series and B's rongzi series (same series selected
--    by attribution_type), for W ∈ {5, 20, 60, 120, 255}.
--    NULL when fewer than W overlapping (date, margin) pairs are available
--    on or before `date`.
--
--    Self-pairs (A = B) are NOT materialized — self-correlation is always 1.
--    Order convention: security_code < benchmark_code (lexicographic) to
--    deduplicate (A,B) vs (B,A). COLLATE "C" forces byte-wise comparison
--    matching Python's default str sort (same trick as industry_correlations).
--
--    Two series × five windows = 10 correlation columns:
--      corr_balance_{5,20,60,120,255}d  — correlation of margin_balance series
--      corr_buy_{5,20,60,120,255}d      — correlation of margin_buy series
--
--    Range: -1.0 (perfect negative) .. +1.0 (perfect positive).
--    NUMERIC(8,4) — same precision as industry_correlations.
--
--  GUARDS
--    Correlation is NULL when either security's series has zero variance in
--    the window OR when either has no data on a given day. Pairs where one
--    side has no series for the attribution (e.g. 'etf' for an index
--    security) are skipped entirely (not materialized).
--
--  Table: analysis.margin_industry_correlation
--    PK: (date, industry_id, security_code, benchmark_code, attribution_type)
--
--  POPULATION  — DEFERRED
--    The legacy Python step (analyze.margins.correlations) computed
--    INDUSTRY-vs-INDUSTRY pairs and has been REMOVED — its semantics no
--    longer match this table. The security-pair rolling-Pearson population
--    is to be re-implemented later (Python + cuDF, or SQL). The table is
--    created EMPTY here so the schema is authoritative; the
--    analysis_identity row is still registered.
--
--  Register in analysis.analysis_identity
--  (name='margin_industry_correlation').
-- ============================================================================

-- Drop the legacy table name (renamed from margin_industry_etf_correlation).
-- Also remove the old identity row.
DELETE FROM analysis.analysis_identity WHERE name = 'margin_industry_etf_correlation';
DROP TABLE IF EXISTS analysis.margin_industry_etf_correlation;

-- Schema change: the table was previously keyed by INDUSTRY pairs
-- (industry_id, benchmark_industry_id) with attribution ∈ {stock,etf,total}.
-- It is now keyed by SECURITY pairs within one industry
-- (industry_id, security_code, benchmark_code) with attribution ∈
-- {index,etf}. The old rows are semantically invalid, and the
-- population step is deferred (see header above), so a clean DROP +
-- CREATE is safe and required — CREATE TABLE IF NOT EXISTS would NOT
-- migrate the existing live table's columns / PK / CHECKs.
DROP TABLE IF EXISTS analysis.margin_industry_correlation;

CREATE TABLE IF NOT EXISTS analysis.margin_industry_correlation (
    industry_id                       TEXT          NOT NULL,
    security_code                     TEXT          NOT NULL,
    benchmark_code                    TEXT          NOT NULL,
    attribution_type                  TEXT          NOT NULL,  -- 'index' | 'etf'
    date                              DATE          NOT NULL,

    -- corr_balance_Wd: rolling W-day Pearson correlation between the two
    -- securities' margin_balance series (selected by attribution_type).
    corr_balance_5d                   NUMERIC(8,4),
    corr_balance_20d                  NUMERIC(8,4),
    corr_balance_60d                  NUMERIC(8,4),
    corr_balance_120d                 NUMERIC(8,4),
    corr_balance_255d                 NUMERIC(8,4),

    -- corr_buy_Wd: rolling W-day Pearson correlation between the two
    -- securities' margin_buy series (selected by attribution_type).
    corr_buy_5d                       NUMERIC(8,4),
    corr_buy_20d                      NUMERIC(8,4),
    corr_buy_60d                      NUMERIC(8,4),
    corr_buy_120d                     NUMERIC(8,4),
    corr_buy_255d                     NUMERIC(8,4),

    CONSTRAINT pk_margin_industry_correlation PRIMARY KEY
        (date, industry_id, security_code, benchmark_code, attribution_type),
    CONSTRAINT chk_margin_industry_corr_attr
        CHECK (attribution_type IN ('index', 'etf')),
    -- COLLATE "C" forces byte-wise comparison so the lexicographic ordering
    -- invariant matches Python's default str sort (same as
    -- industry_correlations). The database default collation (en_US.UTF-8)
    -- would sort punctuation before letters and cause CHECK violations.
    CONSTRAINT chk_margin_industry_corr_order
        CHECK (security_code COLLATE "C" < benchmark_code COLLATE "C")
);

-- Idempotent migration: ADD COLUMN IF NOT EXISTS for all 10 correlation
-- columns. No-op on fresh installs. The legacy corr_sell_* columns (which
-- correlated rq_sell_qty — sec borrow) are DROPPED because the spec restricts
-- this table to rongzi (融资) only.
ALTER TABLE analysis.margin_industry_correlation ADD COLUMN IF NOT EXISTS corr_balance_5d    NUMERIC(8,4);
ALTER TABLE analysis.margin_industry_correlation ADD COLUMN IF NOT EXISTS corr_balance_20d   NUMERIC(8,4);
ALTER TABLE analysis.margin_industry_correlation ADD COLUMN IF NOT EXISTS corr_balance_60d   NUMERIC(8,4);
ALTER TABLE analysis.margin_industry_correlation ADD COLUMN IF NOT EXISTS corr_balance_120d  NUMERIC(8,4);
ALTER TABLE analysis.margin_industry_correlation ADD COLUMN IF NOT EXISTS corr_balance_255d  NUMERIC(8,4);
ALTER TABLE analysis.margin_industry_correlation ADD COLUMN IF NOT EXISTS corr_buy_5d        NUMERIC(8,4);
ALTER TABLE analysis.margin_industry_correlation ADD COLUMN IF NOT EXISTS corr_buy_20d       NUMERIC(8,4);
ALTER TABLE analysis.margin_industry_correlation ADD COLUMN IF NOT EXISTS corr_buy_60d       NUMERIC(8,4);
ALTER TABLE analysis.margin_industry_correlation ADD COLUMN IF NOT EXISTS corr_buy_120d      NUMERIC(8,4);
ALTER TABLE analysis.margin_industry_correlation ADD COLUMN IF NOT EXISTS corr_buy_255d      NUMERIC(8,4);
ALTER TABLE analysis.margin_industry_correlation DROP COLUMN IF EXISTS corr_sell_5d;
ALTER TABLE analysis.margin_industry_correlation DROP COLUMN IF EXISTS corr_sell_20d;
ALTER TABLE analysis.margin_industry_correlation DROP COLUMN IF EXISTS corr_sell_60d;
ALTER TABLE analysis.margin_industry_correlation DROP COLUMN IF EXISTS corr_sell_120d;
ALTER TABLE analysis.margin_industry_correlation DROP COLUMN IF EXISTS corr_sell_255d;

-- Indexes (mirror industry_correlations):
--   1. Per-pair time series (fetch by industry + security pair + attribution_type).
--   2. Reverse-pair lookup (benchmark_code first within the industry).
--   3. Per-industry + per-date snapshots.
CREATE INDEX IF NOT EXISTS idx_margin_industry_corr_pair_attr_date
    ON analysis.margin_industry_correlation
    (industry_id, security_code, benchmark_code, attribution_type, date);
CREATE INDEX IF NOT EXISTS idx_margin_industry_corr_bench_pair_attr_date
    ON analysis.margin_industry_correlation
    (industry_id, benchmark_code, security_code, attribution_type, date);
CREATE INDEX IF NOT EXISTS idx_margin_industry_corr_industry_date
    ON analysis.margin_industry_correlation (industry_id, date);
CREATE INDEX IF NOT EXISTS idx_margin_industry_corr_date
    ON analysis.margin_industry_correlation (date);

COMMENT ON TABLE  analysis.margin_industry_correlation                  IS 'Pairwise rolling Pearson correlation of RONGZI (融资) margin flows/balances between two SECURITIES within the SAME industry, over 5/20/60/120/255-day windows. RONQIN (融券 / sec borrow) EXCLUDED. One row per (date, industry_id, security_code, benchmark_code, attribution_type). A "security" is an ETF code or an index code; ALL pairs within an industry are materialized (index<->index, index<->etf, etf<->etf). attribution_type: index=weighted-avg constituent-stock margin (via analysis.margin_index_series VIEW; for an ETF uses its tracking index, for an index uses itself), etf=the security''s own ETF margin (NULL for index securities). 10 columns = 2 series × 5 windows. Range -1.0..+1.0. Self-pairs excluded; order convention security_code < benchmark_code (COLLATE "C"). Convention mirrors analysis.industry_correlations. POPULATION DEFERRED — legacy industry-pair Python step removed; security-pair rolling-Pearson to be re-implemented later. Table created empty here; schema is authoritative.';
COMMENT ON COLUMN analysis.margin_industry_correlation.industry_id                IS 'Shared industry of both securities in the pair. Derived from the index classification: an ETF inherits its tracking index''s industry_id; an index carries its own. Same industry_id space as analysis.margin_industry_stats.';
COMMENT ON COLUMN analysis.margin_industry_correlation.security_code              IS 'Subject security code (lexicographically smaller of the two). An ETF code (e.g. 510050.SS) or an index code (e.g. 000300).';
COMMENT ON COLUMN analysis.margin_industry_correlation.benchmark_code             IS 'Benchmark security code (lexicographically larger of the two). ETF or index code.';
COMMENT ON COLUMN analysis.margin_industry_correlation.attribution_type           IS 'Which rongzi series is correlated for each security: index (weighted-avg constituent-stock margin from analysis.margin_index_series) or etf (the security''s own ETF margin).';
COMMENT ON COLUMN analysis.margin_industry_correlation.date                       IS 'End date of the rolling correlation window.';
COMMENT ON COLUMN analysis.margin_industry_correlation.corr_balance_5d            IS 'Rolling 5-trading-day Pearson correlation between the two securities'' margin_balance series (selected by attribution_type). NULL when < 5 overlapping days or zero variance in either series. High positive = rongzi outstanding moves together; negative = divergence.';
COMMENT ON COLUMN analysis.margin_industry_correlation.corr_balance_20d           IS 'Rolling 20-trading-day Pearson correlation between margin_balance series. NULL when < 20 overlapping days or zero variance.';
COMMENT ON COLUMN analysis.margin_industry_correlation.corr_balance_60d           IS 'Rolling 60-trading-day Pearson correlation between margin_balance series. NULL when < 60 overlapping days or zero variance.';
COMMENT ON COLUMN analysis.margin_industry_correlation.corr_balance_120d          IS 'Rolling 120-trading-day Pearson correlation between margin_balance series. NULL when < 120 overlapping days or zero variance.';
COMMENT ON COLUMN analysis.margin_industry_correlation.corr_balance_255d          IS 'Rolling 255-trading-day Pearson correlation between margin_balance series. NULL when < 255 overlapping days or zero variance.';
COMMENT ON COLUMN analysis.margin_industry_correlation.corr_buy_5d               IS 'Rolling 5-trading-day Pearson correlation between the two securities'' margin_buy series (daily rongzi BUY flow). NULL when < 5 overlapping days or zero variance. Measures whether daily rongzi BUY flows into the two securities move together.';
COMMENT ON COLUMN analysis.margin_industry_correlation.corr_buy_20d              IS 'Rolling 20-trading-day Pearson correlation between margin_buy series. NULL when < 20 overlapping days or zero variance.';
COMMENT ON COLUMN analysis.margin_industry_correlation.corr_buy_60d              IS 'Rolling 60-trading-day Pearson correlation between margin_buy series. NULL when < 60 overlapping days or zero variance.';
COMMENT ON COLUMN analysis.margin_industry_correlation.corr_buy_120d             IS 'Rolling 120-trading-day Pearson correlation between margin_buy series. NULL when < 120 overlapping days or zero variance.';
COMMENT ON COLUMN analysis.margin_industry_correlation.corr_buy_255d             IS 'Rolling 255-trading-day Pearson correlation between margin_buy series. NULL when < 255 overlapping days or zero variance.';

-- ----------------------------------------------------------------------------
--  Register in analysis.analysis_identity
-- ----------------------------------------------------------------------------
INSERT INTO analysis.analysis_identity (name, detail_name, summary_name, last_run_datetime, description) VALUES
    ('margin_industry_correlation', 'margin_industry_correlation', NULL, NOW(),
     'Pairwise rolling Pearson correlation of RONGZI (融资) margin flows/balances between two SECURITIES within the SAME industry, over 5/20/60/120/255-day windows. RONQIN (融券 / sec borrow) EXCLUDED. One row per (date, industry_id, security_code, benchmark_code, attribution_type). A "security" is an ETF code or an index code; ALL pairs within an industry materialized (index<->index, index<->etf, etf<->etf). attribution_type: index=weighted-avg constituent-stock margin (analysis.margin_index_series VIEW), etf=security''s own ETF margin. 10 columns = 2 series × 5 windows. Range -1.0..+1.0. Self-pairs excluded; order convention security_code < benchmark_code (COLLATE "C"). Convention mirrors analysis.industry_correlations. POPULATION DEFERRED — legacy industry-pair Python step removed; security-pair rolling-Pearson to be re-implemented later. Table created empty; schema authoritative.')
ON CONFLICT (name) DO UPDATE SET
    detail_name       = EXCLUDED.detail_name,
    summary_name      = EXCLUDED.summary_name,
    last_run_datetime = NOW(),
    description       = EXCLUDED.description;


-- ============================================================================
--  Margin Index Series (VIEW) — per-(index_code, date) weighted-average
--  RONGZI (融资) margin series aggregated from constituent stocks.
--
--  For each index code and trading date:
--    index_margin_balance = SUM(stock.rz_balance × parent_index_weight)
--                              FILTER (WHERE rz_balance > 0)
--                            / SUM(parent_index_weight)
--                              FILTER (WHERE rz_balance > 0)
--    index_margin_buy     = SUM(stock.rz_buy × parent_index_weight)
--                              FILTER (WHERE rz_buy > 0)
--                            / SUM(parent_index_weight)
--                              FILTER (WHERE rz_buy > 0)
--
--  INVALID-VALUE EXCLUSION: constituents with rz_balance = 0 / NULL
--  (or rz_buy = 0 / NULL for the buy-flow column) are excluded from
--  BOTH numerator AND denominator via FILTER clauses. A 0 rz_balance
--  means "no rongzi position" — including it in the weighted average
--  would drag the index series toward 0 and overstate coverage (40% of
--  stock rows and 84% of ETF rows are 0). This matches the
--  "skip-the-date-as-a-holiday; denominator does not count for null"
--  rule applied in the Python MA computation (analyze.margins.compute).
--  n_constituents still counts ALL constituents (incl. 0-rz) for
--  diagnostic; n_with_balance = count with rz_balance > 0.
--
--  Stock→index mapping uses EVERY sec_classification stock row
--  (type='stock', parent_index_code <> '', parent_index_weight > 0).
--  A stock may be a constituent of MULTIPLE indices (one row per
--  qualifying index, weight > 2%, non-broad), so it contributes to EACH
--  of them — each index aggregates its OWN full constituent set. The PK
--  (code, parent_index_code) guarantees one row per (stock, index), so
--  no stock is double-counted within a single index's aggregation.
--  BROAD-MARKET indices (000300 etc.) are excluded from stock rows by
--  build_classification, so the stock-based branch yields no series for
--  them. To still serve an 'index' attribution for broad-market /
--  strategy indices, a SECOND branch (UNION ALL) aggregates the margin
--  of their TRACKING ETFs (stats.etf_liquidity_margin JOIN sec_classification
--  type='etf', parent_index_is_primary=TRUE) weighted by
--  parent_index_weight. This ETF-proxy branch is restricted to index codes
--  that have NO stock constituents (NOT IN the stock parent_index_code
--  set), so industry indices (000970 etc. which have BOTH stocks and
--  tracking ETFs) keep their stock-based series only — no double-count.
--  The proxy is a weighted-AVERAGE of ETF margin, semantically a
--  market-implied index margin (the ETFs' own rz_balance/buy), NOT a
--  true constituent-stock aggregation.
--
--  The weighted AVERAGE (not raw SUM) normalizes for partial index
--  coverage — a stock's rongzi is scaled by its weight in the index,
--  then divided by total weight, yielding a per-index margin series
--  comparable across indices of different size / coverage. (Contrast
--  analysis.margin_industry_stats.stock_margin_*, which is an unweighted
--  SUM at the industry level.)
--
--  industry_id is denormalized via a join to the index's OWN
--  sec_classification row (type='index') so callers can group / filter by
--  industry. NULL when the index code has no classification row.
--
--  This VIEW is the source of the 'index' attribution (and the index
--  component of 'total') for analysis.margin_industry_correlation. It is
--  NOT materialized — computed on read. The "active-rongzi in last 30
--  days" universe filter (applied in the Python build of the materialized
--  margin_*_stats tables) is NOT applied here; apply downstream if needed.
--
--  RONQIN (融券 / sec borrow) EXCLUDED — rz_* only.
-- ============================================================================
CREATE OR REPLACE VIEW analysis.margin_index_series AS
SELECT
    sc_par.parent_index_code                          AS index_code,
    idx.industry_id                                   AS industry_id,
    m.date                                            AS date,
    SUM(m.rz_balance * sc_par.parent_index_weight)
        FILTER (WHERE m.rz_balance > 0)
        / NULLIF(SUM(sc_par.parent_index_weight)
                 FILTER (WHERE m.rz_balance > 0), 0)  AS index_margin_balance,
    SUM(m.rz_buy * sc_par.parent_index_weight)
        FILTER (WHERE m.rz_buy > 0)
        / NULLIF(SUM(sc_par.parent_index_weight)
                 FILTER (WHERE m.rz_buy > 0), 0)      AS index_margin_buy,
    COUNT(*)                                          AS n_constituents,
    COUNT(*) FILTER (WHERE m.rz_balance > 0)          AS n_with_balance
FROM stats.stock_liquidity_margin m
JOIN stats.sec_classification sc_par
    ON sc_par.code = m.code
   AND sc_par.type = 'stock'
   AND sc_par.parent_index_code <> ''
   AND sc_par.parent_index_weight IS NOT NULL
   AND sc_par.parent_index_weight > 0
LEFT JOIN stats.sec_classification idx
    ON idx.code = sc_par.parent_index_code
   AND idx.type = 'index'
GROUP BY sc_par.parent_index_code, idx.industry_id, m.date
UNION ALL
SELECT
    sc_par.parent_index_code                          AS index_code,
    idx.industry_id                                   AS industry_id,
    m.date                                            AS date,
    SUM(m.rz_balance * COALESCE(sc_par.parent_index_weight, 1.0))
        FILTER (WHERE m.rz_balance > 0)
        / NULLIF(SUM(COALESCE(sc_par.parent_index_weight, 1.0))
                 FILTER (WHERE m.rz_balance > 0), 0)
                                                     AS index_margin_balance,
    SUM(m.rz_buy * COALESCE(sc_par.parent_index_weight, 1.0))
        FILTER (WHERE m.rz_buy > 0)
        / NULLIF(SUM(COALESCE(sc_par.parent_index_weight, 1.0))
                 FILTER (WHERE m.rz_buy > 0), 0)
                                                     AS index_margin_buy,
    COUNT(*)                                          AS n_constituents,
    COUNT(*) FILTER (WHERE m.rz_balance > 0)          AS n_with_balance
FROM stats.etf_liquidity_margin m
JOIN stats.sec_classification sc_par
    ON sc_par.code = m.code
   AND sc_par.type = 'etf'
   AND sc_par.parent_index_is_primary = TRUE
   AND sc_par.parent_index_code <> ''
LEFT JOIN stats.sec_classification idx
    ON idx.code = sc_par.parent_index_code
   AND idx.type = 'index'
WHERE sc_par.parent_index_code NOT IN (
    SELECT parent_index_code
    FROM stats.sec_classification
    WHERE type = 'stock'
      AND parent_index_code IS NOT NULL
      AND parent_index_code <> ''
)
GROUP BY sc_par.parent_index_code, idx.industry_id, m.date;

COMMENT ON VIEW  analysis.margin_index_series              IS 'Per-(index_code, date) weighted-average RONGZI (融资) margin series. Branch 1 (stock-based): index_margin_{balance,buy} = SUM(stock.rz_* × parent_index_weight) / SUM(parent_index_weight) for indices that have stock constituents in sec_classification (industry indices). Branch 2 (ETF-proxy, UNION ALL): for index codes with NO stock constituents (broad-market 000300 / strategy indices), aggregates the weighted-average margin of their TRACKING ETFs (stats.etf_liquidity_margin, type=''etf'', parent_index_is_primary=TRUE) as a market-implied proxy. industry_id denormalized from the index''s own sec_classification row (type=''index''). Source of the ''index'' attribution (and the index component of ''total'') for analysis.margin_industry_correlation. NOT materialized — computed on read. RONQIN (融券) EXCLUDED.';
COMMENT ON COLUMN analysis.margin_index_series.index_code            IS 'Index code (bare 6-digit, e.g. 000970 for materials, 000300 for CSI300). For stock-based rows: parent_index_code of constituent stocks. For ETF-proxy rows: parent_index_code of tracking ETFs (broad-market/strategy indices that have no stock constituents).';
COMMENT ON COLUMN analysis.margin_index_series.industry_id           IS 'Industry of the index, denormalized from the index''s own sec_classification row (type=''index''). NULL when the index code has no classification row.';
COMMENT ON COLUMN analysis.margin_index_series.date                  IS 'Trading date from stats.stock_liquidity_margin (stock branch) or stats.etf_liquidity_margin (ETF-proxy branch).';
COMMENT ON COLUMN analysis.margin_index_series.index_margin_balance  IS 'Weighted-average rongzi outstanding (yuan): SUM(rz_balance × parent_index_weight) / SUM(parent_index_weight). Stock rz_balance for the stock branch; ETF rz_balance for the ETF-proxy branch. rz_balance = 融资余额 (cumulative).';
COMMENT ON COLUMN analysis.margin_index_series.index_margin_buy      IS 'Weighted-average daily rongzi BUY amount (yuan, FLOW): SUM(rz_buy × parent_index_weight) / SUM(parent_index_weight). rz_buy = 融资买入额.';
COMMENT ON COLUMN analysis.margin_index_series.n_constituents        IS 'Number of constituents with a margin row on this date (includes rz_balance=0). Stocks for the stock branch; ETFs for the ETF-proxy branch.';
COMMENT ON COLUMN analysis.margin_index_series.n_with_balance        IS 'Number of constituents with NON-ZERO rz_balance on this date.';
