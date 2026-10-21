-- ============================================================================
--  Pair Pct-Change Ratios — INDUSTRY x INDUSTRY HEDGE RATIOS, two modes
--  (raw + market-offset), stride-grid materialization.
--
--  Table: analysis.pair_pct_change_ratios
--    PK: (code, benchmark_code, benchmark_market, "interval", date, grain)
--
--  WHAT IT STORES (per subject INDUSTRY vs exposure INDUSTRY vs a
--  broad-market benchmark INDEX, on stride-grid dates ONLY — the corr
--  materialization pattern, see builds.cross_stats --corr /
--  compute/_gpu_corr.fetch_corr_grid_dates): the MINIMUM-VARIANCE HEDGE
--  RATIO h* = −Cov(A, B) / Var(A) — how many units of the subject
--  industry A (the hedging instrument) to hold per unit long of the
--  benchmark industry B (the position) so the combined position's
--  variance is minimal:
--    h* > 0   window covariance NEGATIVE (the "opposite moves" regime)
--             — BUY that many units of A per unit of B;
--    h* < 0   covariance positive — short A to hedge B.
--
--  2026-10-17 REWORKS — (1) industry vs industry (was: index grain +
--  AVG-of-members industry grain): BOTH pair sides are industry
--  aggregates; single-index denominators print 2-decimal closes whose
--  near-flat days (|pct| < 5bp; e.g. 000015 on 2024-10-23 at −0.0003%)
--  blew daily ratios up to ±7,000. (2) hedge-ratio form: Cov/Var over
--  the window (rolling-mean identities — no division by single-day
--  values). (3) THREE CODES + TWO MODES:
--    hedge_raw_ma{N}d     RAW: A, B = the industries' aggregate daily
--                         pct changes (ind_pct). Raw corr(科技,红利)
--                         = +0.42 — the market factor dominates and raw
--                         hedge ratios are typically negative.
--    hedge_offset_ma{N}d  MARKET-OFFSET: A' = A − M, B' = B − M where M
--                         = the benchmark_market INDEX's own-calendar
--                         daily pct. The market factor cancels — offset
--                         corr(科技,红利) = −0.49 vs 000300, −0.70 vs
--                         000001 — and h* answers "how much A to BUY
--                         per unit B" in the opposite-moves regime
--                         (h60 ≈ +0.8 for 科技 hedging 红利).
--
--  MATH (per subject industry A, exposure industry B, market M, window
--  N, grid date d)
--    pct_change_X(t)  = close_X(t) / close_X(prev trading day of X's OWN
--                       calendar) − 1 per MEMBER INDEX (diff-before-join
--                       convention).
--    ind_pct(i, d)    = null-skipped MEAN of the member indexes' pct
--                       changes at date d (active type='index' members
--                       with closes; undefined member days skipped,
--                       never zero-filled).
--    m(d)             = benchmark_market index's OWN-calendar pct.
--    h*_raw{N}(d)     = −Cov(ind_pct(A), ind_pct(B)) / Var(ind_pct(A))
--                       over the trailing N-trading-day window.
--    h*_off{N}(d)     = −Cov(A−M, B−M) / Var(A−M) over the window.
--                       Cov/Var via rolling means (E[AB] − E[A]E[B];
--                       the k/(k−1) sample correction cancels);
--                       windows over the mode's COMMON trading days.
--    NULL rules       corr-parity POOLED CALENDAR (the offsets
--                       convention): each mode's windows are the
--                       trailing N COMMON trading days of that mode
--                       (raw: dates where both industries have
--                       aggregates; offset: dates where both plus the
--                       market print exist) — a date only one side
--                       trades is NOT a day of the pair's calendar, so
--                       the LIVE frontier sample is always a full
--                       window. Null data shrinks the common-day
--                       history: a pair with fewer than ceil(0.9·N)
--                       complete days (young/sparse pairs — the >10%
--                       null rule) emits NULL (min_periods →
--                       5/18/54/230). Var(A) ≤ 1e-16 (near-motionless
--                       instrument) emits NULL. |h*| > 9999.999999 →
--                       NULL (NUMERIC(10,6) backstop).
--
--  GRAIN (static.label_dim domain 'pair_grain'; SMALLINT code)
--    grain = 1 'industry' on EVERY row: code = subject industry_id,
--    benchmark_code = exposure industry_id. Self-industry pairs
--    (A = B) are excluded (h ≡ 1 by construction).
--
--  UNIVERSE
--    Subjects = exposure industries = ALL industries with at least one
--    active type='index' member carrying a close (~115).
--    benchmark_market = the materialized offset markets (default
--    '000300' 沪深300 — the only benchmark materialized in
--    analysis_composites.industry_corr_benchmark_offsets).
--
--  POPULATION
--    python -m analysis.pair_pct_change_ratios
--      incremental: stride-grid dates with no row yet for the stride,
--      plus the LATEST grid date while it is within 10 TRADING days
--      of the calendar frontier (corr parity — the offsets
--      RECENT_STRIDE_GRACE_DAYS), plus the LIVE FRONTIER DATE whenever
--      it is past the last grid date (the mutable off-grid trailing
--      sample — the offsets table's convention: the freshest windows
--      plot at the frontier so the chart's right edge is never up to a
--      stride-interval stale; stride 20 ≈ a month. Each run refreshes
--      the frontier sample and purges the previous frontier's
--      leftovers); heals FULL history when the universe grew.
--      --force: truncate + recompute everything.
--      --stride N (default 20): materialize an alternative grid —
--        "interval" is a PK member, so grids coexist.
--      --code ID[,ID...]: recompute ALL grid dates for the chosen
--        subject INDUSTRY ids only (UI refresh; upsert, no truncate).
--
--  Register in analysis.analysis_identity (name='pair_pct_change_ratios').
-- ============================================================================

CREATE TABLE IF NOT EXISTS analysis.pair_pct_change_ratios (
    code             TEXT   NOT NULL,  -- subject industry_id (hedging instrument)
    benchmark_code   TEXT   NOT NULL,  -- exposure industry_id (the position)
    benchmark_market TEXT   NOT NULL DEFAULT '000300',  -- offset-market index code
    "interval"       INTEGER NOT NULL DEFAULT 20,  -- stride (trading days) of the grid
    date             DATE   NOT NULL,  -- stride-grid date (global index calendar)
    grain            SMALLINT NOT NULL,  -- static.label_dim 'pair_grain': 1=industry (every row)

    -- Minimum-variance hedge ratio h* = -Cov(A,B)/Var(A) per trailing
    -- window N (raw mode: A,B = industry aggregate pct changes). NULL
    -- when the window's pairwise-complete null share exceeds 10%
    -- (min_periods = ceil(0.9·N): 5/18/54/230), Var(A) is at the floor,
    -- or beyond the NUMERIC(10,6) bound.
    hedge_raw_ma5d    NUMERIC(10,6),
    hedge_raw_ma20d   NUMERIC(10,6),
    hedge_raw_ma60d   NUMERIC(10,6),
    hedge_raw_ma255d  NUMERIC(10,6),

    -- Same ratio on MARKET-OFFSET series A' = A - M, B' = B - M (M =
    -- benchmark_market index pct; pairwise-complete days additionally
    -- require the market print that date).
    hedge_offset_ma5d    NUMERIC(10,6),
    hedge_offset_ma20d   NUMERIC(10,6),
    hedge_offset_ma60d   NUMERIC(10,6),
    hedge_offset_ma255d  NUMERIC(10,6),

    CONSTRAINT pk_pair_pct_change_ratios PRIMARY KEY
        (code, benchmark_code, benchmark_market, "interval", date, grain)
) PARTITION BY HASH (code);

-- Native hash partitions (8) keyed on code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('analysis', 'pair_pct_change_ratios', 8);

-- Indexes: the per-pair chart query is a PK-prefix scan, but the API's
-- universe listing (page load / selection change) GROUPs BY code /
-- benchmark_code with ONLY grain + "interval" filters — without these
-- two COVERING indexes it seq-scans the whole table (the INCLUDE
-- columns keep the aggregates index-only: COUNT(DISTINCT counterpart),
-- MIN/MAX date).
CREATE INDEX IF NOT EXISTS idx_pair_pct_ratios_grain_code
    ON analysis.pair_pct_change_ratios (grain, "interval", code)
    INCLUDE (benchmark_code, date);
CREATE INDEX IF NOT EXISTS idx_pair_pct_ratios_grain_benchmark
    ON analysis.pair_pct_change_ratios (grain, "interval", benchmark_code)
    INCLUDE (code, date);

COMMENT ON TABLE  analysis.pair_pct_change_ratios IS 'Industry x industry HEDGE RATIOS at two modes (grain 1 only, static.label_dim pair_grain). THREE CODES per row: code = subject industry (hedging instrument), benchmark_code = exposure industry (the position), benchmark_market = broad-market index subtracted in the offset mode. Value = minimum-variance hedge ratio h* = −Cov(A,B)/Var(A) over trailing 5/20/60/255-day windows — units of the subject to hold per unit long of the benchmark industry; h* > 0 (negative covariance, the opposite-moves regime) = BUY that many units of the subject, h* < 0 = short it. hedge_raw_*: A,B = industry aggregate daily pct changes (null-skipped member means). hedge_offset_*: A'' = A − market pct, B'' = B − market pct (market factor cancelled; e.g. 科技 vs 红利 raw corr +0.42 but −0.49 vs 000300 — the offset mode carries the buy-to-hedge answer). Window statistics count pairwise-complete days only (raw: both industries; offset: both + market); null data skipped, never zero-filled; window null share > 10% emits NULL (min_periods = ceil(0.9·N)); Var(A) ≤ 1e-16 emits NULL; |h*| > 9999.999999 dropped. Materialized on stride-grid dates of the global index calendar (grid position % interval = interval − 1; interval default 20, PK member; self-industry pairs excluded) PLUS one mutable off-grid LIVE sample at the calendar frontier whenever it is past the last grid date (refreshed every incremental run; stale off-grid leftovers purged). Built by python -m analysis.pair_pct_change_ratios (incremental / --force / --stride / --code filtered by industry_id).';
COMMENT ON COLUMN analysis.pair_pct_change_ratios.code IS 'Subject industry_id — the hedging instrument (h* units of it per unit of the exposure industry).';
COMMENT ON COLUMN analysis.pair_pct_change_ratios.benchmark_code IS 'Exposure industry_id — the position being hedged.';
COMMENT ON COLUMN analysis.pair_pct_change_ratios.benchmark_market IS 'Broad-market index code whose own-calendar daily pct is subtracted from BOTH industries in the offset mode (default ''000300'' 沪深300).';
COMMENT ON COLUMN analysis.pair_pct_change_ratios."interval" IS 'Stride in trading days between consecutive grid dates on the global index calendar (default 20). PK member, so several strides can coexist.';
COMMENT ON COLUMN analysis.pair_pct_change_ratios.date IS 'Stride-grid date (global index calendar positions idx %% interval = interval - 1). Values are sampled only here — the daily hedge series is not stored.';
COMMENT ON COLUMN analysis.pair_pct_change_ratios.grain IS 'Static.label_dim domain pair_grain: 1 = ''industry'' on every row — both pair sides are industry aggregates (2026-10-17 rework; grain 0 ''index'' rows are no longer produced).';
COMMENT ON COLUMN analysis.pair_pct_change_ratios.hedge_raw_ma5d IS 'Minimum-variance hedge ratio h* = -Cov(A,B)/Var(A), trailing 5 days, RAW mode (A,B = industry aggregate pct changes; pairwise-complete days; min_periods = 5). h* > 0 = buy the subject to hedge the exposure industry.';
COMMENT ON COLUMN analysis.pair_pct_change_ratios.hedge_raw_ma20d IS 'Minimum-variance hedge ratio h* = -Cov(A,B)/Var(A), trailing 20 days, RAW mode (pairwise-complete days; min_periods = 18).';
COMMENT ON COLUMN analysis.pair_pct_change_ratios.hedge_raw_ma60d IS 'Minimum-variance hedge ratio h* = -Cov(A,B)/Var(A), trailing 60 days, RAW mode (pairwise-complete days; min_periods = 54).';
COMMENT ON COLUMN analysis.pair_pct_change_ratios.hedge_raw_ma255d IS 'Minimum-variance hedge ratio h* = -Cov(A,B)/Var(A), trailing 255 days, RAW mode (pairwise-complete days; min_periods = 230).';
COMMENT ON COLUMN analysis.pair_pct_change_ratios.hedge_offset_ma5d IS 'Minimum-variance hedge ratio on market-offset series A''=A−M, B''=B−M (M = benchmark_market pct), trailing 5 days (pairwise-complete incl. the market print; min_periods = 5). The opposite-moves regime after the market factor is cancelled.';
COMMENT ON COLUMN analysis.pair_pct_change_ratios.hedge_offset_ma20d IS 'Minimum-variance hedge ratio on market-offset series, trailing 20 days (min_periods = 18).';
COMMENT ON COLUMN analysis.pair_pct_change_ratios.hedge_offset_ma60d IS 'Minimum-variance hedge ratio on market-offset series, trailing 60 days (min_periods = 54).';
COMMENT ON COLUMN analysis.pair_pct_change_ratios.hedge_offset_ma255d IS 'Minimum-variance hedge ratio on market-offset series, trailing 255 days (min_periods = 230).';

-- ----------------------------------------------------------------------------
--  Register in analysis.analysis_identity
-- ----------------------------------------------------------------------------
INSERT INTO analysis.analysis_identity (name, detail_name, summary_name, last_run_datetime, description) VALUES
    ('pair_pct_change_ratios', 'pair_pct_change_ratios', NULL, NOW(),
     'Industry x industry HEDGE RATIOS at two modes (grain 1 only, static.label_dim pair_grain). THREE CODES per row: code = the subject industry (the hedging instrument), benchmark_code = the exposure industry (the position), benchmark_market = the broad-market index whose pct change is subtracted in the offset mode. The stored value is the minimum-variance hedge ratio h* = −Cov(A, B) / Var(A) over trailing 5/20/60/255-day windows — how many units of the subject to hold per unit long of the benchmark industry so the combined position''s variance is minimal; h* > 0 (negative covariance — the opposite-moves regime) = BUY that many units of the subject, h* < 0 = short it. hedge_raw_ma{N}d: A and B are the industries'' aggregate daily pct changes (ind_pct = null-skipped mean over member indexes'' OWN-calendar pcts). hedge_offset_ma{N}d: A'' = A − market pct, B'' = B − market pct (excess over the benchmark market index; the market factor cancels — e.g. 科技 vs 红利 raw corr +0.42 but −0.49 vs 000300, −0.70 vs 000001, so the offset mode is where the buy-to-hedge answer lives). Window statistics count pairwise-complete days only (raw: both industries; offset: both plus the market): null data is skipped, never zero-filled, and a window whose null share exceeds 10% emits NULL (min_periods = ceil(0.9·N)); Var(A) at/below 1e-16 emits NULL; |h*| > 9999.999999 dropped. Materialized on stride-grid dates of the global index calendar (grid position % interval = interval − 1; default 20, PK member so strides coexist; self-industry pairs excluded) PLUS one mutable off-grid LIVE sample at the calendar frontier whenever it is past the last grid date (refreshed every incremental run; stale off-grid leftovers purged). Subjects = exposure industries = ALL industries with active type=''index'' members carrying closes. Source: stats.index_basic_stats closes + stats.sec_classification. Built by python -m analysis.pair_pct_change_ratios (incremental / --force / --stride / --code filtered by industry_id).')
ON CONFLICT (name) DO UPDATE SET
    detail_name       = EXCLUDED.detail_name,
    summary_name      = EXCLUDED.summary_name,
    last_run_datetime = NOW(),
    description       = EXCLUDED.description;
