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
--  Populated by `analysis.mov_ave_spread` (internal RSI step in rsi.py;
--  incremental upsert by missing dates; --force truncates first). No FK to
--  mov_ave_spreads_detail — data integrity is guaranteed by INNER JOINs on
--  basic_stats in the build script.
-- ----------------------------------------------------------------------------
CREATE TABLE analysis.mov_ave_rsi (
    sec_type        SMALLINT     NOT NULL,  -- label code (stats.label_dim 'sec_type'): 0=etf 1=index 2=stock
    code            TEXT         NOT NULL,
    date            DATE         NOT NULL,

    -- Per-window tail-episode population flag (the re-entry semantics
    -- live HERE, in the analysis layer — computed by steps/rsi_typical.py
    -- from mov_ave_rsi_ranks' full-history ranks):
    --   NULL  the day's RSI is OUTSIDE both 25% band tails for the window
    --   TRUE  the day is an ISOLATED / first-touch tail episode day
    --         (group-first, or gap from the prior tail day > 5 trading days)
    --   FALSE the day is a RE-ENTRY registration (the same episode
    --         re-qualifying after a 1..5-trading-day exit — market-holiday
    --         closures / short flickers)
    -- The side (top = overbought / bottom = oversold) follows from which
    -- band the day's rank ratio falls in; consumers derive it from their
    -- own rank/percentile read.
    is_typical_3days  BOOLEAN,
    is_typical_6days  BOOLEAN,
    is_typical_10days BOOLEAN,
    is_typical_14days BOOLEAN,
    is_typical_20days BOOLEAN,
    is_typical_60days BOOLEAN,

    -- Per-window tail-episode SIZE for EVERY in-band day (the same
    -- episode segmentation as the flags — start / re-entry / gap-1
    -- continuation days all carry it; NULL = outside both bands): the
    -- episode's trading-day count over the code's FULL history. The
    -- curve tier's population marker + episode weight (w = 1/size —
    -- each episode contributes total weight exactly 1, so registrations
    -- and continuations count per EPISODE, not per day). Computed and
    -- rewritten wholesale by steps/rsi_typical.py on every ranks
    -- rebuild, beside the flags.
    typical_ep_size_3days  SMALLINT,
    typical_ep_size_6days  SMALLINT,
    typical_ep_size_10days SMALLINT,
    typical_ep_size_14days SMALLINT,
    typical_ep_size_20days SMALLINT,
    typical_ep_size_60days SMALLINT,

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
COMMENT ON COLUMN analysis.mov_ave_rsi.sec_type    IS 'Security type as its stats.label_dim (''sec_type'' domain) SMALLINT code: 0 = etf (ETF), 1 = index (CSI-style index), 2 = stock (individual equity).';
COMMENT ON COLUMN analysis.mov_ave_rsi.code        IS 'Ticker. ETFs bare 6-digit (e.g. "510050"); indices bare code (e.g. "000300"); stocks keep their exchange suffix (e.g. "600008.SS").';
COMMENT ON COLUMN analysis.mov_ave_rsi.date        IS 'Business date (trading day).';
COMMENT ON COLUMN analysis.mov_ave_rsi.rsi_3days   IS 'Wilder RSI over 3 trading days (alpha=1/3, ewm adjust=False, min_periods=3) — an ultra-short momentum window. 0..100. NULL until 3 periods.';
COMMENT ON COLUMN analysis.mov_ave_rsi.rsi_6days   IS 'Wilder RSI over 6 trading days (alpha=1/6, ewm adjust=False, min_periods=6). 0..100. NULL until 6 consecutive gain/loss observations.';
COMMENT ON COLUMN analysis.mov_ave_rsi.rsi_10days  IS 'Wilder RSI over 10 trading days (alpha=1/10, ewm adjust=False, min_periods=10). 0..100. NULL until 10 periods.';
COMMENT ON COLUMN analysis.mov_ave_rsi.rsi_14days  IS 'Wilder RSI over 14 trading days (alpha=1/14, ewm adjust=False, min_periods=14) — the classic Wilder window. 0..100. NULL until 14 periods.';
COMMENT ON COLUMN analysis.mov_ave_rsi.rsi_20days  IS 'Wilder RSI over 20 trading days (alpha=1/20, ewm adjust=False, min_periods=20). 0..100. NULL until 20 periods.';
COMMENT ON COLUMN analysis.mov_ave_rsi.rsi_60days  IS 'Wilder RSI over 60 trading days (alpha=1/60, ewm adjust=False, min_periods=60) — a longer-term momentum window complementing the classic 14-day Wilder default. 0..100. NULL until 60 consecutive gain/loss observations. Computed by analysis.mov_ave_spread (rsi.py) using the same Wilder EWM recurrence as the other RSI windows (delta = price[t]-price[t-1] is cuDF-accelerated via grouped_diff; the per-window EWM stays on pandas because cuDF lacks grouped-ewm support — see rsi.py for the documented rationale).';
COMMENT ON COLUMN analysis.mov_ave_rsi.is_typical_3days  IS 'Tail-episode population flag for the 3-day window (see is_typical_14days).';
COMMENT ON COLUMN analysis.mov_ave_rsi.is_typical_6days  IS 'Tail-episode population flag for the 6-day window (see is_typical_14days).';
COMMENT ON COLUMN analysis.mov_ave_rsi.is_typical_10days IS 'Tail-episode population flag for the 10-day window (see is_typical_14days).';
COMMENT ON COLUMN analysis.mov_ave_rsi.is_typical_14days IS 'Tail-episode population flag for the 14-day window: NULL = the day''s RSI is outside both 25%-band tails of the code''s full-history rank distribution (rank ratio in (0.25, 0.75)); TRUE = the FIRST-TOUCH pass''s days — the episode''s first tail day (group-first, or > 5 trading days since the prior tail day) AND its gap-1 continuation days; FALSE = the RE-ENTRY pass''s days — the same episode re-qualifying after a 1..5-trading-day exit (market-holiday closures / short flickers) AND its gap-1 continuation days (2026-10-07: the continuation days inherit their pass''s registration flag, so every in-band day is drawn on its own pass''s side). Computed by analysis.mov_ave_spread (steps/rsi_typical.py) from analysis.mov_ave_rsi_ranks on every ranks rebuild (band membership shifts when the full-history ranks shift) and rewritten wholesale per sec_type. In-band MEMBERSHIP itself is typical_ep_size_14days (non-NULL = in-band); the side follows from which band the day''s rank ratio is in.';
COMMENT ON COLUMN analysis.mov_ave_rsi.is_typical_20days IS 'Tail-episode population flag for the 20-day window (see is_typical_14days).';
COMMENT ON COLUMN analysis.mov_ave_rsi.is_typical_60days IS 'Tail-episode population flag for the 60-day window (see is_typical_14days).';
COMMENT ON COLUMN analysis.mov_ave_rsi.typical_ep_size_14days IS 'The day''s tail EPISODE SIZE (trading-day count over the code''s full history) for the 14-day window, carried by EVERY in-band day of the episode — first-touch starts, re-entry registrations AND the gap-1 continuation days (which the is_typical flag attributes to their episode pass); NULL = outside both 25% band tails (see is_typical_14days for the segmentation). The curve tier''s population marker + episode weight (w = 1/size: each episode contributes total weight exactly 1, so the depth response counts EPISODES, not days — an episode that burrows deep into the tail would otherwise dominate the fit by its length). Computed by analysis.mov_ave_spread (steps/rsi_typical.py) on every ranks rebuild, wholesale per sec_type beside the flags.';
COMMENT ON COLUMN analysis.mov_ave_rsi.typical_ep_size_3days  IS 'Tail-episode size for the 3-day window (see typical_ep_size_14days).';
COMMENT ON COLUMN analysis.mov_ave_rsi.typical_ep_size_6days  IS 'Tail-episode size for the 6-day window (see typical_ep_size_14days).';
COMMENT ON COLUMN analysis.mov_ave_rsi.typical_ep_size_10days IS 'Tail-episode size for the 10-day window (see typical_ep_size_14days).';
COMMENT ON COLUMN analysis.mov_ave_rsi.typical_ep_size_20days IS 'Tail-episode size for the 20-day window (see typical_ep_size_14days).';
COMMENT ON COLUMN analysis.mov_ave_rsi.typical_ep_size_60days IS 'Tail-episode size for the 60-day window (see typical_ep_size_14days).';


-- ----------------------------------------------------------------------------
--  Table: analysis.mov_ave_rsi_ranks  (per-code RSI history ranks)
--    One row per (sec_type, code, date) — mirrors analysis.mov_ave_rsi 1:1.
--    For each RSI window, stores the ordinal rank of that date's RSI WITHIN
--    THE CODE'S OWN FULL RSI HISTORY (every date whose rsi_{W}days is
--    non-null): rank 1 = the code's highest RSI reading for the window,
--    N = the lowest. Ties break by earlier date (order of appearance in the
--    date-sorted build frame). Ranks are dense 1..N, so MAX(rank) per
--    (sec_type, code, window) equals the count of ranked days and a
--    "top X%" cutoff is rank <= ceil(X% * MAX(rank)).
--
--  rank_rsi_{W}days is NULL wherever the window's RSI is NULL (the first
--  W-1 rows of a code's history — min_periods=W — plus fully-flat
--  gain/loss windows), hence the columns are nullable.
--
--  WHY WHOLESALE REBUILDS: every stored rank can shift whenever ANY new
--  day's RSI lands anywhere in the code's history (a new reading re-ranks
--  the whole distribution and moves every percentile denominator), so
--  incremental per-date upserts would update nearly every row on every
--  run. Populated by analysis.mov_ave_spread (steps/rsi_ranks.py) on every
--  run that recomputes RSI: DELETE the run's sec_type/code scope,
--  recompute from the same in-memory RSI frame (no extra DB round-trip),
--  CSV-COPY insert. Per-code semantics also keep the rebuild valid in
--  --code single-code mode (a per-date cross-sectional rank would need the
--  whole universe's frame, which a single-code run does not have). No FK
--  to mov_ave_rsi — the NO-FK derived-data design of
--  01_mov_ave_spreads_detail.sql.
-- ----------------------------------------------------------------------------
DROP TABLE IF EXISTS analysis.mov_ave_rsi_ranks CASCADE;
DELETE FROM analysis.analysis_identity WHERE name = 'mov_ave_rsi_ranks';

CREATE TABLE analysis.mov_ave_rsi_ranks (
    sec_type        SMALLINT     NOT NULL,  -- label code (stats.label_dim 'sec_type'): 0=etf 1=index 2=stock
    code            TEXT         NOT NULL,
    date            DATE         NOT NULL,

    -- Per-code RSI history rank per window: 1 = highest RSI of the code's
    -- own history, N = lowest (earlier date wins ties); NULL where that
    -- window's RSI is NULL.
    rank_rsi_3days       INTEGER,
    rank_rsi_6days       INTEGER,
    rank_rsi_10days      INTEGER,
    rank_rsi_14days      INTEGER,
    rank_rsi_20days      INTEGER,
    rank_rsi_60days      INTEGER,

    CONSTRAINT pk_mov_ave_rsi_ranks PRIMARY KEY (code, sec_type, date)
) PARTITION BY HASH (code);

-- Native hash partitions (16) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p15.
-- Same layout as mov_ave_rsi so the wholesale DELETE per sec_type and the
-- per-code API reads walk the same partitioning.
SELECT public.create_hash_partitions('analysis', 'mov_ave_rsi_ranks', 16);

-- (sec_type, code, date) index — the sec_type-leading read shape the API
-- uses (one code's rank history per request); same rationale as the
-- mov_ave_rsi index above.
CREATE INDEX IF NOT EXISTS idx_mov_ave_rsi_ranks_sec_type_code_date
    ON analysis.mov_ave_rsi_ranks (sec_type, code, date);

COMMENT ON TABLE  analysis.mov_ave_rsi_ranks             IS 'Per-code RSI history ranks (3/6/10/14/20/60 days): the ordinal rank of each date''s RSI within the code''s own full RSI history — 1 = highest RSI reading, N = lowest, earlier date wins ties, dense 1..N so MAX(rank) per (code, window) is the count of ranked days. Mirrors analysis.mov_ave_rsi row-for-row; rank columns are NULL where that window''s RSI is NULL. Rebuilt wholesale per sec_type by analysis.mov_ave_spread (steps/rsi_ranks.py) on every run that recomputes RSI.';
COMMENT ON COLUMN analysis.mov_ave_rsi_ranks.sec_type    IS 'Security type as its stats.label_dim (''sec_type'' domain) SMALLINT code: 0 = etf (ETF), 1 = index (CSI-style index), 2 = stock (individual equity).';
COMMENT ON COLUMN analysis.mov_ave_rsi_ranks.code        IS 'Ticker. ETFs bare 6-digit (e.g. "510050"); indices bare code (e.g. "000300"); stocks keep their exchange suffix (e.g. "600008.SS").';
COMMENT ON COLUMN analysis.mov_ave_rsi_ranks.date        IS 'Business date (trading day) — matches the mov_ave_rsi row being ranked.';
COMMENT ON COLUMN analysis.mov_ave_rsi_ranks.rank_rsi_3days  IS 'Rank of rsi_3days within the code''s own non-null rsi_3days history: 1 = highest, N = lowest, earlier date wins ties. NULL where rsi_3days is NULL. Dense 1..N.';
COMMENT ON COLUMN analysis.mov_ave_rsi_ranks.rank_rsi_6days  IS 'Rank of rsi_6days within the code''s own non-null rsi_6days history: 1 = highest, N = lowest, earlier date wins ties. NULL where rsi_6days is NULL. Dense 1..N.';
COMMENT ON COLUMN analysis.mov_ave_rsi_ranks.rank_rsi_10days IS 'Rank of rsi_10days within the code''s own non-null rsi_10days history: 1 = highest, N = lowest, earlier date wins ties. NULL where rsi_10days is NULL. Dense 1..N.';
COMMENT ON COLUMN analysis.mov_ave_rsi_ranks.rank_rsi_14days IS 'Rank of rsi_14days within the code''s own non-null rsi_14days history: 1 = highest, N = lowest, earlier date wins ties. NULL where rsi_14days is NULL. Dense 1..N.';
COMMENT ON COLUMN analysis.mov_ave_rsi_ranks.rank_rsi_20days IS 'Rank of rsi_20days within the code''s own non-null rsi_20days history: 1 = highest, N = lowest, earlier date wins ties. NULL where rsi_20days is NULL. Dense 1..N.';
COMMENT ON COLUMN analysis.mov_ave_rsi_ranks.rank_rsi_60days IS 'Rank of rsi_60days within the code''s own non-null rsi_60days history: 1 = highest, N = lowest, earlier date wins ties. NULL where rsi_60days is NULL. Dense 1..N. Computed by analysis.mov_ave_spread (steps/rsi_ranks.py) from the RSI step''s in-memory full-history frame — one whole-frame grouped rank per window (groupby(sec_type, code).rank(method="first", ascending=False)) on the cudf.pandas proxy.';
