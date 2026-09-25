-- ============================================================================
--  mov_ave_spread suite — split of the former 03_mov_ave_spreads.sql.
--  Shared conventions (sec_type handling, the NO-FK derived-data design,
--  rebuild semantics): see 01_mov_ave_spreads_detail.sql.
-- ============================================================================

-- Drop any prior version so a re-run rebuilds the table from the CREATE
-- below; also remove the stale analysis_identity row so the registration
-- starts clean.
DROP TABLE IF EXISTS analysis.mov_ave_rsi CASCADE;
DELETE FROM analysis.analysis_identity WHERE name = 'mov_ave_rsi';

-- ----------------------------------------------------------------------------
--  Table: analysis.mov_ave_rsi  (per-asset+date Wilder RSI)
--    One row per (sec_type, code, date). Stores Wilder's Relative Strength
--    Index for 6 windows (3/6/10/14/20/60 days).
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

    -- Wilder RSI columns (0..100, NULL until N periods).
    -- Windows: 3 (ultra-short) / 6 / 10 / 14 (classic Wilder) / 20 / 60
    -- (~3 trading months).
    rsi_3days       NUMERIC(10,6),
    rsi_6days       NUMERIC(10,6),
    rsi_10days      NUMERIC(10,6),
    rsi_14days      NUMERIC(10,6),
    rsi_20days      NUMERIC(10,6),
    rsi_60days      NUMERIC(10,6),

    CONSTRAINT pk_mov_ave_rsi PRIMARY KEY (code, sec_type, date)
) PARTITION BY HASH (code);

-- Native hash partitions (16) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p15
SELECT public.create_hash_partitions('analysis', 'mov_ave_rsi', 16);

-- NOTE: the PK (code, sec_type, date) serves per-code lookups, but it
-- CANNOT serve sec_type-scoped reads — sec_type is its SECOND column, so
-- a build/read filtered on sec_type alone (analysis_forecasts' input
-- fetch hash-builds its LEFT JOIN side on exactly that) degraded to a
-- full seq scan of all 16 partitions (~9.4M rows / ~1 GB to match ~8% of
-- the table; observed 2026-09-25). A (sec_type, code, date) index was
-- previously created and dropped here as a PK duplicate — that rationale
-- holds for per-code equality lookups only; this index exists for the
-- sec_type-leading build-side shape. The added index-maintenance cost on
-- the mov_ave_spread build's INSERTs is the trade.
CREATE INDEX IF NOT EXISTS idx_mov_ave_rsi_sec_type_code_date
    ON analysis.mov_ave_rsi (sec_type, code, date);

COMMENT ON TABLE  analysis.mov_ave_rsi             IS 'Wilder RSI (3/6/10/14/20/60 days). One row per (code, sec_type, date). sec_type ∈ {etf, index, stock}. analysis.mov_ave_rsi_holiday FK-references this table ON DELETE CASCADE.';
COMMENT ON COLUMN analysis.mov_ave_rsi.sec_type    IS 'Security type: etf (ETF), index (CSI-style index), or stock (individual equity).';
COMMENT ON COLUMN analysis.mov_ave_rsi.code        IS 'Ticker. ETFs use exchange suffix (e.g. "510050.SS"); indices use bare code (e.g. "000300").';
COMMENT ON COLUMN analysis.mov_ave_rsi.date        IS 'Business date (trading day).';
COMMENT ON COLUMN analysis.mov_ave_rsi.rsi_3days   IS 'Wilder RSI over 3 trading days (alpha=1/3, ewm adjust=False, min_periods=3) — an ultra-short momentum window. 0..100. NULL until 3 periods.';
COMMENT ON COLUMN analysis.mov_ave_rsi.rsi_6days   IS 'Wilder RSI over 6 trading days (alpha=1/6, ewm adjust=False, min_periods=6). 0..100. NULL until 6 consecutive gain/loss observations.';
COMMENT ON COLUMN analysis.mov_ave_rsi.rsi_10days  IS 'Wilder RSI over 10 trading days (alpha=1/10, ewm adjust=False, min_periods=10). 0..100. NULL until 10 periods.';
COMMENT ON COLUMN analysis.mov_ave_rsi.rsi_14days  IS 'Wilder RSI over 14 trading days (alpha=1/14, ewm adjust=False, min_periods=14) — the classic Wilder window. 0..100. NULL until 14 periods.';
COMMENT ON COLUMN analysis.mov_ave_rsi.rsi_20days  IS 'Wilder RSI over 20 trading days (alpha=1/20, ewm adjust=False, min_periods=20). 0..100. NULL until 20 periods.';
COMMENT ON COLUMN analysis.mov_ave_rsi.rsi_60days  IS 'Wilder RSI over 60 trading days (alpha=1/60, ewm adjust=False, min_periods=60) — a longer-term momentum window complementing the classic 14-day Wilder default. 0..100. NULL until 60 consecutive gain/loss observations. Computed by analyze.mov_ave_spread (rsi.py) using the same Wilder EWM recurrence as the other RSI windows (delta = price[t]-price[t-1] is cuDF-accelerated via grouped_diff; the per-window EWM stays on pandas because cuDF lacks grouped-ewm support — see rsi.py for the documented rationale).';
