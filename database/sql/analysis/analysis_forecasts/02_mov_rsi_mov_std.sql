-- ============================================================================
--  Tables: analysis_forecasts.mov_rsi + analysis_forecasts.mov_std
--
--  The MOTIVATION (bucket-defining) tables of the forecast analysis.
--  Each row identifies ONE extreme-day bucket within the trailing
--  5-year window (stat_month - 5y, stat_month] of the code's own
--  trading days, stores the bucket's motivation stats, and links via
--  forecast_id to its RESULT rows in analysis_forecasts.forecast_results
--  (1:N — one forecast_id → 4 period rows: next / 5d / 20d / mixed).
--
--  RETIRED FAMILY: the mov_gap motivation table (N-day price-return
--  extreme-percentile buckets; former 03_mov_gap.sql) was REMOVED
--  2026-09 — the gap indicator columns are gone from
--  analysis.mov_ave_rsi, the forecast stage no longer computes the
--  family, and no signals engine consumes it. The DROP below also
--  purges the family's forecast_results + forecast_identities rows
--  (the registry rows go last — the result purge resolves through
--  them). Idempotent: no-ops once the table is gone.
--
--  CODE-CLUSTERED, forecast_id-keyed (2026-09): PK (code, forecast_id)
--  on HASH (code) partitions — the code-clustered read/write axis (a
--  per-security read prunes to ONE partition and walks the code-leading
--  PK; forecast_id-only joins/searches use the secondary
--  idx_mov_rsi_forecast_id / idx_mov_std_forecast_id). code is the ONLY
--  identity column stored here (the partition key); sec_type and
--  stat_month live ONLY in analysis_forecasts.forecast_identities (one
--  registry row per forecast_id, written in the same transaction),
--  which is also the search-by-identity table; the family-unique metric
--  columns below (rsi_window / side / pct / regime_state) are plain
--  NOT NULL columns — functionally dependent on forecast_id (the writer
--  allocates one id per bucket), kept out of the PK.
--
--  Full-window gate: a code enters a stat_month only once its OWN
--  history spans the whole window (first data date <= window start) —
--  a code first listed 2020-01 first appears in the 2025-01 snapshot;
--  earlier stat_months have no rows for it (no partial-window stats).
--
--    mov_rsi — RSI extreme-percentile buckets:
--      a day is in the bucket when rsi_{W}days sits in the top pct%
--      (side=top, overbought) or bottom pct% (side=bottom, oversold)
--      of the window's non-NULL rsi_{W}days values (percentile computed
--      per code over the window, linear interpolation).
--      STREAK-MERGE (2026-09, replacing the legacy fixed-5-day
--      cooldown): consecutive bucket days are ONE forecast signal,
--      anchored at the run's MID day (the ((L-1)//2 + 1)-th day — the
--      high_low_streaks convention); the bucket's mean run length is
--      recorded on forecast_identities.streak_signal_days.
--      RSI windows mirror analysis.mov_ave_rsi:
--        3 / 6 / 10 / 14 (classic Wilder) / 20 / 60 days.
--      pct ∈ {1, 5, 10, 25} (top 1% = the highest-1%-RSI days, etc.).
--
--    mov_std — Bollinger-breach buckets:
--      a day breaches the UPPER bound when
--        price > ma_{W} + k * std_{W}days
--      and the LOWER bound when price < ma_{W} - k * std_{W}days
--      (ma_{W} from stats.{sec_type}_tech_stats, std_{W}days from
--      analysis.mov_ave_spreads_detail — the same inputs the parent
--      mov_ave_spread analysis renders), streak-merged exactly like
--      mov_rsi.
--      k ∈ {0.5, 1.0, 1.5, 2.0, 2.5, 3.0} (σ multiples);
--      ma_window ∈ {5, 20, 60} trading days.
--
--  Motivation columns:
--    regime_state (both) — the bucket's market-regime split: the
--                  stats.market_regimes day label (calm / hot / panic /
--                  quiet) carried by the bucket's trigger days (every
--                  trigger day joins exactly one regime, so each
--                  (config, regime) pair is its own bucket). Replaces
--                  the retired is_market_hyped boolean (2026-09 regime
--                  refactor; hot is the closest successor of the old
--                  TRUE split). The underlying RSI / band values
--                  are NOT re-stored: rsi_{W}days is already in
--                  analysis.mov_ave_rsi and ma/std are in
--                  analysis.mov_ave_spreads_detail / stats.*_tech_stats,
--                  joined via (sec_type, code, date) and the bucket's
--                  rsi_window / ma_window keys (sec_type / stat_month
--                  come from the bucket's forecast_identities registry
--                  row).
--  (forward-change outcomes are NOT stored here — see forecast_results)
-- ============================================================================

-- ----------------------------------------------------------------------------
--  Retired family: analysis_forecasts.mov_gap (former 03_mov_gap.sql).
--  Purge the linked result + registry rows FIRST (the result delete
--  resolves (sec_type, bucket) through the registry), then DROP the
--  table with its 16 hash partitions (CASCADE, like the retired
--  analysis_signals.signals).
-- ----------------------------------------------------------------------------

DELETE FROM analysis_forecasts.forecast_results f
USING analysis_forecasts.forecast_identities i
WHERE i.forecast_id = f.forecast_id AND i.bucket = 'mov_gap';

DELETE FROM analysis_forecasts.forecast_identities
WHERE bucket = 'mov_gap';

DROP TABLE IF EXISTS analysis_forecasts.mov_gap CASCADE;

-- ----------------------------------------------------------------------------
--  Table: analysis_forecasts.mov_rsi
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS analysis_forecasts.mov_rsi (
    code            TEXT         NOT NULL,  -- hash partition key + PK lead; sec_type / stat_month live in forecast_identities
    forecast_id     BIGINT       NOT NULL,  -- 1:N link to the bucket's 4 forecast_results period rows; id-only joins/searches use idx_mov_rsi_forecast_id
    rsi_window      INTEGER      NOT NULL,  -- RSI window in trading days: 3/6/10/14/20/60
    side            TEXT         NOT NULL,  -- 'top' (overbought) | 'bottom' (oversold)
    pct             INTEGER      NOT NULL,  -- percentile width: 1 / 5 / 10 / 25

    -- recorded build parameter (NOT PK): trailing window the bucket was
    -- computed over — '5y' = (stat_month - 5y, stat_month]
    lookback_period TEXT         NOT NULL DEFAULT '5y',

    -- motivation cols
    regime_state    TEXT         NOT NULL,  -- the bucket's market-regime split (stats.market_regimes day label of its trigger days)

    CONSTRAINT pk_mov_rsi PRIMARY KEY (code, forecast_id),
    CONSTRAINT ck_mov_rsi_regime
        CHECK (regime_state IN ('calm', 'hot', 'panic', 'quiet'))
) PARTITION BY HASH (code);

SELECT public.create_hash_partitions('analysis_forecasts', 'mov_rsi', 16);

-- ----------------------------------------------------------------------------
--  Table: analysis_forecasts.mov_std
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS analysis_forecasts.mov_std (
    code            TEXT         NOT NULL,  -- hash partition key + PK lead; sec_type / stat_month live in forecast_identities
    forecast_id     BIGINT       NOT NULL,  -- 1:N link to the bucket's 4 forecast_results period rows; id-only joins/searches use idx_mov_std_forecast_id
    ma_window       INTEGER      NOT NULL,  -- MA window in trading days: 5/20/60
    k               NUMERIC(4,2) NOT NULL,  -- σ multiple: 0.5/1.0/1.5/2.0/2.5/3.0
    side            TEXT         NOT NULL,  -- 'upper' | 'lower'

    -- recorded build parameter (NOT PK): trailing window the bucket was
    -- computed over — '5y' = (stat_month - 5y, stat_month]
    lookback_period TEXT         NOT NULL DEFAULT '5y',

    -- motivation cols
    regime_state    TEXT         NOT NULL,  -- the bucket's market-regime split (stats.market_regimes day label of its breach days)

    CONSTRAINT pk_mov_std PRIMARY KEY (code, forecast_id),
    CONSTRAINT ck_mov_std_regime
        CHECK (regime_state IN ('calm', 'hot', 'panic', 'quiet'))
) PARTITION BY HASH (code);

SELECT public.create_hash_partitions('analysis_forecasts', 'mov_std', 16);

-- forecast_id-only lookups (search-by-id, the gate's registry join) —
-- the PK leads with code, so an id-only predicate needs its own index
CREATE INDEX IF NOT EXISTS idx_mov_rsi_forecast_id
    ON analysis_forecasts.mov_rsi (forecast_id);

CREATE INDEX IF NOT EXISTS idx_mov_std_forecast_id
    ON analysis_forecasts.mov_std (forecast_id);

-- ----------------------------------------------------------------------------
--  Comments — mov_rsi
-- ----------------------------------------------------------------------------
COMMENT ON TABLE analysis_forecasts.mov_rsi IS 'RSI extreme-day bucket definitions (motivation): one row per forecast_id — the days of one security-month whose rsi_{W}days is in the top/bottom pct% of the trailing 5-year window ending at the bucket''s stat_month, streak-merged (2026-09: consecutive bucket days are ONE forecast signal anchored at the run''s MID day — the mean run length per signal lives on forecast_identities.streak_signal_days; the legacy fixed-5-day cooldown was removed), split by the market regime (calm/hot/panic/quiet — stats.market_regimes) its trigger days carry. PK (code, forecast_id) on HASH (code) partitions — code-clustered reads/writes; the identity (sec_type, stat_month) + bucket family live in analysis_forecasts.forecast_identities. Results (forward changes / reversal probabilities) live in analysis_forecasts.forecast_results via forecast_id. Bucket day RSI values join from analysis.mov_ave_rsi on (sec_type, code, date, rsi_window). Source: analysis.mov_ave_rsi + stats.*_basic_stats closes.';
COMMENT ON COLUMN analysis_forecasts.mov_rsi.code IS 'Hash partition key + PK lead. The bucket''s sec_type and stat_month are registered in analysis_forecasts.forecast_identities under the row''s forecast_id.';
COMMENT ON COLUMN analysis_forecasts.mov_rsi.forecast_id IS 'Surrogate id (PK partner of code; 1:N link to the bucket''s 4 period rows in analysis_forecasts.forecast_results, allocated by the writer, shared across all 4 periods). The bucket''s identity (sec_type, stat_month) + bucket family are registered in analysis_forecasts.forecast_identities under this id; id-only lookups use idx_mov_rsi_forecast_id.';
COMMENT ON COLUMN analysis_forecasts.mov_rsi.rsi_window IS 'RSI window (trading days) whose extreme days are bucketed: 3/6/10/14/20/60 — mirrors analysis.mov_ave_rsi.rsi_*days.';
COMMENT ON COLUMN analysis_forecasts.mov_rsi.side IS 'Bucket side: top = rsi in the top pct% of the window (overbought; reversals are changes below the bucket''s FIXED 1% threshold (0.01 — the period-end n-day close vs ±1%); bottom = rsi in the bottom pct% (oversold; reversals are changes above it).';
COMMENT ON COLUMN analysis_forecasts.mov_rsi.pct IS 'Percentile width of the bucket: 1, 5, 10 or 25 (percent). The threshold is the window''s (linear-interpolated) percentile of rsi_{W}days over non-NULL values.';
COMMENT ON COLUMN analysis_forecasts.mov_rsi.regime_state IS 'The bucket''s market-regime split: the stats.market_regimes day label (calm / hot / panic / quiet) carried by the bucket''s trigger days — every trigger day joins exactly one regime, so each (config, regime) pair is its own bucket. Replaces the retired is_market_hyped boolean (stats.mov_ave_market_hypes episode overlap); hot is the closest successor of the old TRUE split.';
COMMENT ON COLUMN analysis_forecasts.mov_rsi.lookback_period IS 'Recorded build parameter (NOT a PK member): the trailing calendar window the bucket was computed over — ''5y'' = (stat_month - 5 years, stat_month]. Default ''5y''; a rebuild with a different lookback requires --force.';

-- ----------------------------------------------------------------------------
--  Comments — mov_std
-- ----------------------------------------------------------------------------
COMMENT ON TABLE analysis_forecasts.mov_std IS 'Bollinger-breach bucket definitions (motivation): one row per forecast_id — the days of one security-month within the trailing 5-year window ending at the bucket''s stat_month whose price closed beyond ma_{W} ± k·std_{W}days, streak-merged (2026-09: consecutive breach days are ONE forecast signal anchored at the run''s MID day — the mean run length per signal lives on forecast_identities.streak_signal_days; the legacy fixed-5-day cooldown was removed). PK (code, forecast_id) on HASH (code) partitions — code-clustered reads/writes; the identity (sec_type, stat_month) + bucket family live in analysis_forecasts.forecast_identities. Results (forward changes / reversal probabilities) live in forecast_results linked via forecast_id. Band inputs join from analysis.mov_ave_spreads_detail / stats.*_tech_stats. Sources: stats.*_tech_stats (ma), analysis.mov_ave_spreads_detail (std), stats.*_basic_stats closes (price, COALESCE etf_adjustment.adj_close for ETFs).';
COMMENT ON COLUMN analysis_forecasts.mov_std.code IS 'Hash partition key + PK lead. The bucket''s sec_type and stat_month are registered in analysis_forecasts.forecast_identities under the row''s forecast_id.';
COMMENT ON COLUMN analysis_forecasts.mov_std.forecast_id IS 'Surrogate id (PK partner of code; 1:N link to the bucket''s 4 period rows in analysis_forecasts.forecast_results, allocated by the writer, shared across all 4 periods). The bucket''s identity (sec_type, stat_month) + bucket family are registered in analysis_forecasts.forecast_identities under this id; id-only lookups use idx_mov_std_forecast_id.';
COMMENT ON COLUMN analysis_forecasts.mov_std.ma_window IS 'MA/σ window (trading days): 5/20/60 — ma_{W} from stats.*_tech_stats, std_{W}days from analysis.mov_ave_spreads_detail.';
COMMENT ON COLUMN analysis_forecasts.mov_std.k IS 'σ multiple defining the Bollinger bound: 0.5 / 1.0 / 1.5 / 2.0 / 2.5 / 3.0.';
COMMENT ON COLUMN analysis_forecasts.mov_std.side IS 'Breach side: upper = price > ma_{W} + k·std_{W}days (reversals are changes below the bucket''s FIXED 1% threshold (0.01 — the period-end n-day close vs ±1%); lower = price < ma_{W} - k·std_{W}days (reversals are changes above it).';
COMMENT ON COLUMN analysis_forecasts.mov_std.regime_state IS 'The bucket''s market-regime split: the stats.market_regimes day label (calm / hot / panic / quiet) carried by the bucket''s breach days — every breach day joins exactly one regime, so each (config, regime) pair is its own bucket. Replaces the retired is_market_hyped boolean (stats.mov_ave_market_hypes episode overlap); hot is the closest successor of the old TRUE split.';
COMMENT ON COLUMN analysis_forecasts.mov_std.lookback_period IS 'Recorded build parameter (NOT a PK member): the trailing calendar window the bucket was computed over — ''5y'' = (stat_month - 5 years, stat_month]. Default ''5y''; a rebuild with a different lookback requires --force.';

-- ----------------------------------------------------------------------------
--  Data-quality gate: the mov_rsi vocabularies (shared helpers, see
--  01_forecast_results.sql / 00_partition_utils.sql). NOT VALID first,
--  validated once by the schema-wide sweep below.
-- ----------------------------------------------------------------------------
SELECT public.ensure_check_constraint(
    'analysis_forecasts.mov_rsi',
    'chk_mov_rsi_side',
    $chk$side IN ('top', 'bottom')$chk$);
SELECT public.validate_pending_checks('analysis_forecasts');

-- ----------------------------------------------------------------------------
--  Data-quality gate: the mov_std vocabularies (shared helpers, see
--  01_forecast_results.sql / 00_partition_utils.sql). NOT VALID first,
--  validated once by the schema-wide sweep below.
-- ----------------------------------------------------------------------------
SELECT public.ensure_check_constraint(
    'analysis_forecasts.mov_std',
    'chk_mov_std_side',
    $chk$side IN ('upper', 'lower')$chk$);
SELECT public.validate_pending_checks('analysis_forecasts');
