-- ============================================================================
--  Volatility — per-(sec_type, code, date) realized-volatility panel
--  from daily bars (NOT options implied vol — that lives in
--  analysis.options_vol_index).
--
--    Table: analysis.volatility
--    PK: (sec_type, code, date) — one row per security per trading day
--
--  STRUCTURED FOR ALL SECURITIES — the sec_type column carries the full
--  shared domain (0=etf 1=index 2=stock 3=industry; the pipeline's
--  config maps each security sec_type to its own
--  stats.{etf,index,stock}_basic_stats / *_identity / *_intraday_5min
--  sources). No FK: a multi-sec_type table cannot reference one
--  identity table (the analysis.market_crowdedness precedent). Rows
--  exist only where the security has a daily bar that day.
--
--  RUN SCOPE (2026-11-18): index only — the 10 study indices
--  (STUDY_INDEX_CODES per the study-scope rule); widening = the
--  pipeline's ENABLED_SEC_TYPES + universe config, no schema change.
--  Industry rows stay out of scope (pool grain, not a security).
--
--  POPULATION
--    analysis.volatility (Python module). Per project rule, ALL
--    INSERTs are in Python — no raw INSERT...SELECT SQL in this file.
--    Incremental by missing dates (anti-join against the sec_type's
--    own daily-bar calendar, scoped to the run's codes); --force
--    deletes the resolved sec_types' rows and regenerates.
--
--    SOURCES (per sec_type, mapped in analysis/volatility/config.py)
--      daily bars   — stats.{etf,index,stock}_basic_stats
--                     (open/high/low/close). CAVEAT: is_ohl_estimated
--                     rows (close-only CSIndex publications before
--                     2024-10-24) have O ≈ H ≈ L ≈ C — the range-based
--                     estimators (Parkinson / GK / RS / YZ /
--                     overnight-intraday split / ATR / NATR) degenerate
--                     toward the close-to-close value there; they stay
--                     computable, just less informative.
--      intraday     — stats.{etf,index,stock}_intraday_5min 5-minute
--                     bar closes for the realized-vol leg. The
--                     intraday tables are pruned to a ~90-day retention
--                     window, so rv_5min / jump_ratio_5min are NULL on
--                     older dates BY DESIGN.
--
--  UNITS AND CONVENTIONS
--    Annualization: every *vol* column is the DAILY vol × sqrt(252) × 100
--    (percent annualized — 23.45 means 23.45% a year; the repo-standard
--    252 trading-day year).
--    Log returns: r_t = ln(close_t / close_{t-1}); ln ratios of OHLC
--    throughout (no simple-return conversions).
--    Windows are TRADING ROWS. A vol column is NULL until its window has
--    enough rows (W rows for the W-day stds, 2 for EWMA, 15 for ATR-14).
--    Percentile ranks follow the house trailing-252 convention
--    (share of non-NULL trailing values <= today's, 0..1, >= 60 obs,
--    see analysis.market_participation.*_pct_rank_252); z-scores use the
--    trailing 252-row mean/std SHIFTED 1 row (>= 60 obs; house
--    convention from stats.market_regimes.vol_z — today's value scored
--    against the PAST year, never against a window that includes
--    itself).
--
--  METRIC DEFINITIONS
--    close-to-close HV   hv_{W} = std(r, ddof=1) over trailing W rows
--                        × sqrt(252) × 100, W ∈ (5, 10, 20, 60, 120, 252).
--    EWMA vol            ewma_vol: RiskMetrics recursion applied
--                        contemporaneously (the realized level, not the
--                        one-day-ahead forecast):
--                        σ²_t = 0.94·σ²_{t-1} + 0.06·r²_t
--                        (λ = 0.94 daily decay, J.P. Morgan RiskMetrics
--                        1996; initialized at the first squared return),
--                        × sqrt(252) × 100.
--    Parkinson           parkinson_vol_20: mean of the daily
--                        ln(H/L)²/(4·ln2) terms over trailing 20 rows
--                        (high-low range only; zero-drift assumption),
--                        annualized.
--    Garman-Klass        garman_klass_vol_20: mean of
--                        0.5·ln(H/L)² − (2·ln2 − 1)·ln(C/O)² terms
--                        (OHLC; ~7.4× the efficiency of close-to-close
--                        under zero-drift Brownian motion), annualized.
--    Rogers-Satchell     rogers_satchel_vol_20: mean of
--                        ln(H/C)·ln(H/O) + ln(L/C)·ln(L/O) terms
--                        (handles non-zero drift, not opening gaps),
--                        annualized.
--    Yang-Zhang          yang_zhang_vol_20:
--                        σ²_overnight + k·σ²_open + (1−k)·σ²_RS over the
--                        trailing 20 rows with k = 0.34/(1.34 + (n+1)/
--                        (n−1)), n = 20 (handles BOTH overnight gaps and
--                        drift — the most complete daily-OHLC estimator),
--                        annualized.
--    overnight/intraday  overnight_vol_20 = std(ln(O_t/C_{t-1}), 20 rows)
--                        (the gap leg); intraday_vol_20 =
--                        std(ln(C_t/O_t), 20 rows) (the open-to-close
--                        leg); both annualized. The A-share gap regime
--                        read: which leg carries the vol.
--    ATR / NATR          atr_14 = Wilder smoothing (alpha = 1/14,
--                        adjust=False) of the true range
--                        TR = max(H−L, |H−C_prev|, |L−C_prev|) — a
--                        PRICE-LEVEL range gauge (yuan), NOT annualized;
--                        natr_14 = atr_14 / close × 100 (percent of
--                        level, comparable across securities).
--    Bollinger width     boll_bw_20 = (4 · std(close, 20 rows, ddof=0)) /
--                        ma20 × 100 — the classic band-width vol gauge
--                        (σ of the PRICE LEVEL, matching the boll20
--                        signal family's own σ, not a return vol).
--    vol-of-vol          vol_of_vol_20 = std(hv_20, 20 rows, ddof=1)
--                        in annualized-vol points — how unstable the
--                        vol estimate itself is.
--    vol ratio           vol_ratio_20_252 = hv_20 / hv_252 — short/long
--                        term-structure read: > 1 vol expanding, < 1
--                        compressing.
--    percentile ranks    hv_20_pct_rank_252 / ewma_vol_pct_rank_252 /
--                        yang_zhang_vol_20_pct_rank_252 /
--                        natr_14_pct_rank_252 — where today's value sits
--                        in its own trailing-252-row distribution
--                        (0..1; >= 60 obs).
--    z-scores            hv_20_z_252 / ewma_vol_z_252 — (x − μ_252) /
--                        σ_252 of the PAST year (shifted 1 row).
--    5-min realized vol  rv_5min = sqrt( Σ r_i² over the day's
--                        intra-session 5-min log closes × 252 ) × 100
--                        (overnight gap excluded — bar-to-bar within the
--                        session only); jump_ratio_5min =
--                        max(0, RV/BPV − 1) with BPV the bipower
--                        variation (π/2)·Σ|r_i|·|r_{i+1}| — the realized-
--                        variance jump gauge (values > 0 flag
--                        jump-dominated days); rv_bar_count = the day's
--                        5-min bar count (quality gauge; NULL with the
--                        rv leg).
--
--    Rows exist only where the security has a daily bar that day; all
--    metric columns beyond the PK are NULL-gated by their own warmup.
--  ============================================================================
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: Each security's daily realized-volatility panel — close-to-close historical vol across six windows, the RiskMetrics EWMA vol, the Parkinson/Garman-Klass/Rogers-Satchell/Yang-Zhang OHLC estimators, the overnight-vs-intraday split, ATR/NATR, Bollinger band width, vol-of-vol and short/long vol ratio, trailing-year percentile and z-score regime gauges, and the 5-minute realized vol where intraday bars exist.
-- tags: volatility, 波动率, realized volatility, 已实现波动率, historical volatility, 历史波动率, ewma, parkinson, garman-klass, rogers-satchell, yang-zhang, atr, natr, bollinger band width, 布林带宽, vol-of-vol, 波动率的波动率, vol regime, 波动率状态, percentile, 分位数, z-score, intraday, 盘中, five-minute bars, 五分钟K线, variance, 方差, index, 指数, etf, stock, 股票


CREATE TABLE IF NOT EXISTS analysis.volatility (
    sec_type                  SMALLINT        NOT NULL,  -- label code (static.label_dim 'sec_type'): 0=etf 1=index 2=stock 3=industry
    code                      TEXT            NOT NULL,
    date                      DATE            NOT NULL,

    -- Close-to-close historical vol (std of daily log returns, ddof=1,
    -- annualized %), one column per window
    hv_5                      NUMERIC(10,4),  -- 5-row  % annualized
    hv_10                     NUMERIC(10,4),  -- 10-row % annualized
    hv_20                     NUMERIC(10,4),  -- 20-row % annualized
    hv_60                     NUMERIC(10,4),  -- 60-row % annualized
    hv_120                    NUMERIC(10,4),  -- 120-row % annualized
    hv_252                    NUMERIC(10,4),  -- 252-row % annualized

    -- RiskMetrics EWMA vol (λ=0.94), annualized %
    ewma_vol                  NUMERIC(10,4),

    -- Daily-OHLC range estimators (20-row mean of daily variance terms,
    -- annualized %)
    parkinson_vol_20          NUMERIC(10,4),  -- high-low range
    garman_klass_vol_20       NUMERIC(10,4),  -- OHLC
    rogers_satchell_vol_20    NUMERIC(10,4),  -- drift-robust OHLC
    yang_zhang_vol_20         NUMERIC(10,4),  -- gap + drift robust

    -- Overnight vs intraday decomposition (20-row, annualized %)
    overnight_vol_20          NUMERIC(10,4),  -- std of ln(O/C_prev)
    intraday_vol_20           NUMERIC(10,4),  -- std of ln(C/O)

    -- ATR family (Wilder 14)
    atr_14                    NUMERIC(18,4),  -- price-level true range (yuan), NOT annualized
    natr_14                   NUMERIC(10,4),  -- atr_14 / close × 100

    -- Band width / vol structure gauges
    boll_bw_20                NUMERIC(10,4),  -- 4·σ_price20 / ma20 × 100 (price-level σ, boll family)
    vol_of_vol_20             NUMERIC(10,4),  -- std of hv_20 over 20 rows (vol points)
    vol_ratio_20_252          NUMERIC(10,4),  -- hv_20 / hv_252

    -- Regime gauges: trailing-252-row percentile (0..1, >= 60 obs) and
    -- z-score (past-year moments, shifted 1 row)
    hv_20_pct_rank_252        DOUBLE PRECISION,
    ewma_vol_pct_rank_252     DOUBLE PRECISION,
    yang_zhang_vol_20_pct_rank_252 DOUBLE PRECISION,
    natr_14_pct_rank_252      DOUBLE PRECISION,
    hv_20_z_252               DOUBLE PRECISION,
    ewma_vol_z_252            DOUBLE PRECISION,

    -- 5-minute realized vol leg (NULL where intraday bars were pruned /
    -- never captured)
    rv_5min                   NUMERIC(10,4),  -- Σ r² (5-min log closes) × 252, sqrt, % annualized
    jump_ratio_5min           NUMERIC(10,4),  -- max(0, RV / bipower variation − 1)
    rv_bar_count              INTEGER,        -- the day's 5-min bar count (quality gauge)

    CONSTRAINT pk_volatility PRIMARY KEY (sec_type, code, date),
    CONSTRAINT chk_volatility_sec_type
        CHECK (sec_type IN (0, 1, 2, 3))  -- label codes: 0=etf 1=index 2=stock 3=industry
) PARTITION BY HASH (code);

-- Native hash partitions (8) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('analysis', 'volatility', 8);

CREATE INDEX IF NOT EXISTS idx_volatility_date
    ON analysis.volatility (date);
CREATE INDEX IF NOT EXISTS idx_volatility_sec_type_date
    ON analysis.volatility (sec_type, date);

COMMENT ON TABLE  analysis.volatility IS 'Per-(sec_type, code, date) realized-volatility panel (structured for all securities; run scope = the 10 study indices): close-to-close historical vol across 5/10/20/60/120/252-row windows, RiskMetrics EWMA vol (λ=0.94), the Parkinson / Garman-Klass / Rogers-Satchell / Yang-Zhang daily-OHLC estimators (20-row), the overnight-vs-intraday vol split, ATR(14)/NATR(14), Bollinger band width, vol-of-vol and short/long vol ratio, trailing-252-row percentile ranks and z-scores, and the 5-minute realized vol with bipower jump ratio where intraday bars exist. All *vol* columns are percent annualized (×√252×100) except atr_14 (price level). Built by analysis.volatility from stats.{etf,index,stock}_basic_stats + *_intraday_5min; all INSERTs in Python per project rule.';

COMMENT ON COLUMN analysis.volatility.sec_type    IS 'Security-type label code (static.label_dim ''sec_type''): 0=etf 1=index 2=stock 3=industry. The pipeline''s run scope is index (1) only for now.';
COMMENT ON COLUMN analysis.volatility.code        IS 'Security code (the sec_type''s identity format, e.g. 000001 上证指数).';
COMMENT ON COLUMN analysis.volatility.date        IS 'Trading date.';
COMMENT ON COLUMN analysis.volatility.hv_5        IS 'Close-to-close historical vol: std of daily log returns ln(C/C_prev) (ddof=1) over the trailing 5 rows, ×√252×100 (percent annualized). NULL until 6 rows.';
COMMENT ON COLUMN analysis.volatility.hv_10       IS 'Close-to-close historical vol over the trailing 10 rows, % annualized. NULL until 11 rows.';
COMMENT ON COLUMN analysis.volatility.hv_20       IS 'Close-to-close historical vol over the trailing 20 rows, % annualized. NULL until 21 rows.';
COMMENT ON COLUMN analysis.volatility.hv_60       IS 'Close-to-close historical vol over the trailing 60 rows, % annualized.';
COMMENT ON COLUMN analysis.volatility.hv_120      IS 'Close-to-close historical vol over the trailing 120 rows, % annualized.';
COMMENT ON COLUMN analysis.volatility.hv_252      IS 'Close-to-close historical vol over the trailing 252 rows, % annualized.';
COMMENT ON COLUMN analysis.volatility.ewma_vol    IS 'RiskMetrics EWMA vol, contemporaneous (realized level): σ²_t = 0.94·σ²_{t-1} + 0.06·r²_t (λ=0.94 daily decay, RiskMetrics 1996; initialized at the first squared return), ×√252×100.';
COMMENT ON COLUMN analysis.volatility.parkinson_vol_20       IS 'Parkinson estimator: mean of ln(H/L)²/(4·ln2) over the trailing 20 rows, annualized %. High-low range only (zero-drift assumption); degenerates toward close-to-close on is_ohl_estimated rows.';
COMMENT ON COLUMN analysis.volatility.garman_klass_vol_20    IS 'Garman-Klass estimator: mean of 0.5·ln(H/L)² − (2·ln2−1)·ln(C/O)² over the trailing 20 rows, annualized %. OHLC; ~7.4× close-to-close efficiency under zero-drift Brownian motion; can go slightly negative on degenerate bars (stored as NULL there).';
COMMENT ON COLUMN analysis.volatility.rogers_satchell_vol_20 IS 'Rogers-Satchell estimator: mean of ln(H/C)·ln(H/O) + ln(L/C)·ln(L/O) over the trailing 20 rows, annualized %. Handles non-zero drift; ignores opening gaps.';
COMMENT ON COLUMN analysis.volatility.yang_zhang_vol_20      IS 'Yang-Zhang estimator: σ²_overnight + k·σ²_open + (1−k)·σ²_RS over the trailing 20 rows with k = 0.34/(1.34 + (n+1)/(n−1)), annualized %. Handles BOTH overnight gaps and drift — the reference daily-OHLC estimator.';
COMMENT ON COLUMN analysis.volatility.overnight_vol_20       IS 'Overnight-gap vol: std of ln(O_t/C_{t-1}) over the trailing 20 rows, annualized %.';
COMMENT ON COLUMN analysis.volatility.intraday_vol_20        IS 'Open-to-close intraday vol: std of ln(C_t/O_t) over the trailing 20 rows, annualized %.';
COMMENT ON COLUMN analysis.volatility.atr_14     IS 'ATR(14): Wilder smoothing (alpha=1/14, adjust=False) of the true range TR = max(H−L, |H−C_prev|, |L−C_prev|). PRICE-LEVEL gauge (yuan), NOT annualized.';
COMMENT ON COLUMN analysis.volatility.natr_14    IS 'NATR(14): atr_14 / close × 100 — the level-normalized ATR, comparable across securities.';
COMMENT ON COLUMN analysis.volatility.boll_bw_20 IS 'Bollinger band width: (4 · std(close, 20 rows, ddof=0)) / ma20 × 100. σ of the PRICE LEVEL (matching the boll20 signal family), not a return vol.';
COMMENT ON COLUMN analysis.volatility.vol_of_vol_20 IS 'Vol-of-vol: std of the hv_20 series over the trailing 20 rows (ddof=1), in annualized-vol points — how unstable the vol estimate itself is.';
COMMENT ON COLUMN analysis.volatility.vol_ratio_20_252 IS 'Short/long vol ratio: hv_20 / hv_252. > 1 = vol expanding vs its yearly norm, < 1 = compressing.';
COMMENT ON COLUMN analysis.volatility.hv_20_pct_rank_252 IS 'Share of non-NULL trailing-252-row hv_20 values <= today''s (0..1, >= 60 obs) — the house trailing-percentile convention.';
COMMENT ON COLUMN analysis.volatility.ewma_vol_pct_rank_252 IS 'Trailing-252-row percentile of ewma_vol (0..1, >= 60 obs).';
COMMENT ON COLUMN analysis.volatility.yang_zhang_vol_20_pct_rank_252 IS 'Trailing-252-row percentile of yang_zhang_vol_20 (0..1, >= 60 obs).';
COMMENT ON COLUMN analysis.volatility.natr_14_pct_rank_252 IS 'Trailing-252-row percentile of natr_14 (0..1, >= 60 obs).';
COMMENT ON COLUMN analysis.volatility.hv_20_z_252 IS 'z-score of hv_20 against the trailing 252-row mean/std SHIFTED 1 row, >= 60 obs (today scored vs the PAST year, never vs a window including itself).';
COMMENT ON COLUMN analysis.volatility.ewma_vol_z_252 IS 'z-score of ewma_vol against its trailing 252-row mean/std (shifted 1 row, >= 60 obs).';
COMMENT ON COLUMN analysis.volatility.rv_5min IS 'Realized vol from 5-minute bars: sqrt(Σ r_i² over the day''s intra-session 5-min log closes × 252) × 100 (percent annualized; overnight gap excluded). NULL where the sec_type''s *_intraday_5min table has no bars for the day (retention ≈ 90 days).';
COMMENT ON COLUMN analysis.volatility.jump_ratio_5min IS 'Realized-variance jump gauge: max(0, RV/BPV − 1) with BPV = (π/2)·Σ|r_i|·|r_{i+1}| the bipower variation. > 0 flags jump-dominated sessions; NULL with rv_5min.';
COMMENT ON COLUMN analysis.volatility.rv_bar_count IS 'The day''s 5-minute bar count used for the rv leg (quality gauge; an A-share session yields ~48). NULL with rv_5min.';
