-- ============================================================================
--  Tables: analysis_forecasts.forecast_results
--          + analysis_forecasts.forecast_identities (bottom of this file)
--
--  Normalized RESULT data of the forecast analysis. One row per
--  (forecast_id, period) — each forecast bucket has up to 4 rows
--  (period ∈ {'next', '5d', '20d', '60d'}). The config JSONB is
--  per-forecast (not per-period), duplicated across all 4 period rows.
--
--  forecast_id links 1:1 from analysis_forecasts.mov_rsi / mov_std;
--  PK is (forecast_id, period) so the JOIN naturally expands 1→4.
--  forecast_identities (shared-PK registry, defined at the bottom of
--  this file) resolves each forecast_id to (sec_type, code, stat_month,
--  bucket family) — the identity every motivation table repeats in its
--  leading PK — so a search by forecast_id needs no table probing.
--
--  Columns (consolidated — no period suffix in column name; the
--  ``period`` column carries that role):
--    period           — 'next' (next day), '5d' / '20d' / '60d'
--    ave_change       — mean n-day forward fractional change
--    std_change       — population std-dev of the n-day forward
--                       fractional change over the same valid bucket
--                       days as ave_change (dispersion of the horizon
--                       outcomes; NULL when occurrence_count = 0)
--    max_change       — MAX n-day forward fractional change (close-based)
--                       NULL for period='next'
--    min_change       — MIN n-day forward fractional change (close-based)
--                       NULL for period='next'
--    occurrence_count — bucket days with a VALID n-day forward change
--                       (the mean / reversal-prob denominator)
--    trigger_dates    — DATE[] of the SAME days' calendar dates
--                       (ascending): the exact dates the row's stats were
--                       computed over, so a UI can mark them on the code's
--                       price trend; array length == occurrence_count
--                       (NULL when occurrence_count is 0/NULL). Per-period
--                       row-local: the 60d row lists fewer dates than the
--                       next row when the bucket's trailing days lack a
--                       60-day forward window.
--    trigger_excess   — NUMERIC(10,6)[] element-wise parallel to
--                       trigger_dates: each merged signal's TRIGGER
--                       EXCESS — the mid day's trigger value minus the
--                       bucket's qualifying bar (signed, value − bar;
--                       the live_signals signal_excess convention).
--                       Written by the scalar-bar event engines only
--                       (mov_rsi / mov_gap: indicator − percentile bar;
--                       mov_std: price − breached band edge; mov_pairs /
--                       mov_pairs_ema: the day's spread, the bar being
--                       the zero line); NULL arrays for the state
--                       families (px_vol_state / margin_ratio_state /
--                       opp_pair_state / pe_state / dividend_state —
--                       band membership, no scalar bar) and
--                       high_low_streaks (ex-post streak anchors).
--    max_low_change_ratio — (1 + max_change) / (1 + min_change) =
--                       the best-to-worst n-day ENDPOINT outcome ratio
--                       across the bucket's trigger days (NOT a
--                       within-window path swing)
--                       NULL for period='next' or when no valid days
--    reverse_prob     — P(n-day change is a REVERSAL beyond the row's
--                       reverse_threshold against the bucket side),
--                       over bucket days with a valid n-day forward
--                       change
--    reverse_threshold — the reversal bar reverse_prob was computed
--                       against (fractional). ADAPTIVE since the
--                       2026-09 std-threshold study:
--                       reverse_threshold = k_n · σ(code, stat_month,
--                       n) where σ = population std of the n-day
--                       forward changes over ALL of the code's window
--                       days (the base_rates population) and k_n per
--                       horizon {next: 0.5, 5d: 0.75, 20d: 1.0, 60d:
--                       1.0} — at k·σ the no-edge reversal rate is
--                       Φ(−k) at EVERY horizon, which de-saturates the
--                       20d/60d probabilities the legacy fixed 1% bar
--                       pinned at ≈1.0. Falls back to the fixed 0.01
--                       bar where σ is degenerate. Legacy rows
--                       (pre-migration / "fixed" mode) carry 0.01.
--
--  High/low and max_low_change_ratio are NULL when the denominator is 0
--  or min_change ≤ -1.0 (division guard).
-- ============================================================================

CREATE TABLE IF NOT EXISTS analysis_forecasts.forecast_results (
    forecast_id         BIGINT GENERATED BY DEFAULT AS IDENTITY,

    -- Period of the forward horizon — one of 'next' / '5d' / '20d' / '60d'.
    period              TEXT NOT NULL,

    -- Trailing calendar window the bucket was computed over (recorded
    -- build parameter, NOT a PK member; duplicated across all 4 period
    -- rows like config): '5y' = (stat_month - 5y, stat_month]. A
    -- rebuild with a different lookback requires --force.
    lookback_period     TEXT NOT NULL DEFAULT '5y',

    -- config JSONB: per-bucket motivation/config data that varies by
    -- analysis type. Duplicated across all 4 period rows of the same
    -- forecast_id. Keys depend on the linked mov_* table:
    --   px_vol: {"mean_t": float, "mean_z": float}
    --   margin_ratio: {"mean_ratio": float|null, "mean_z": float|null}
    --   mov_rsi / mov_std / mov_gap: NULL (no config data)
    config              JSONB,
    

    -- mean n-day forward fractional change (e.g. 0.05 = +5%)
    ave_change          NUMERIC(10,6),

    -- population std-dev of the n-day forward fractional change over
    -- the same valid bucket days as ave_change (E[x²] − E[x]² via a
    -- sum-of-squares pass). NULL when no valid days.
    std_change          NUMERIC(10,6),

    -- max/min n-day forward fractional change (close-based).
    -- NULL for period='next' (no close-based high/low at the 1-day
    -- horizon).                                             
    max_change          NUMERIC(10,6),
    min_change          NUMERIC(10,6),

    -- bucket days with a VALID n-day forward change (denominator of
    -- ave_change / reverse_prob)
    occurrence_count    BIGINT,

    -- calendar DATEs of the SAME days (ascending) — the exact dates
    -- this row's stats were computed over. Length == occurrence_count;
    -- NULL when occurrence_count is 0/NULL. Row-local per period (the
    -- later horizons drop trailing days without a full forward window).
    -- Under the 2026-09 streak-merge these are the merged signals' MID
    -- days; streak_starts / streak_ends carry each signal's qualifying
    -- run [start, end] and streak_days its trading-day count
    -- (element-wise parallel; event engines only).
    trigger_dates       DATE[],
    streak_starts       DATE[],
    streak_ends         DATE[],
    streak_days         BIGINT[],

    -- per-signal TRIGGER EXCESS, element-wise parallel to
    -- trigger_dates: the mid day's trigger value minus the bucket's
    -- qualifying bar (signed, value − bar — the live_signals
    -- .signal_excess convention: positive beyond an upper/top bar,
    -- negative below a lower/bottom one; the pairs families' bar is
    -- the zero line, so there the excess IS the day's spread). Only
    -- the scalar-bar event engines write it (mov_rsi / mov_gap:
    -- indicator − percentile bar; mov_std: price − breached band
    -- edge; mov_pairs / mov_pairs_ema: the spread); the state
    -- families (px_vol_state / margin_ratio_state / opp_pair_state /
    -- pe_state / dividend_state — band membership, no scalar bar)
    -- and high_low_streaks (ex-post streak anchors) carry NULL
    -- arrays. Length == occurrence_count; NULL when 0/NULL; row-local
    -- per period like trigger_dates.
    trigger_excess      NUMERIC(10,6)[],

    -- within-window close swing amplitude: (1 + max_change) /
    -- (1 + min_change) = max(close[t+1..t+n]) / min(close[t+1..t+n]).
    -- NULL for period='next' or when no valid days.
    max_low_change_ratio NUMERIC(10,6),

    -- P(forward change reverses beyond reverse_threshold against the
    -- bucket side) over bucket days with a valid n-day forward change.
    -- NUMERIC(8,6): probability ∈ [0,1] but exactly 1.0 must fit —
    -- NUMERIC(6,6) (scale 6 ⇒ |v| < 1) would overflow on all-reverse
    -- buckets.
    reverse_prob        NUMERIC(8,6),

    -- The reversal bar (fractional) this row's reverse_prob was
    -- computed against: the FIXED 1% bar (0.01) since 2026-09-08 —
    -- reverse_prob reads as a plain "P > 1%" on the PERIOD-END close
    -- (at period 5d the event is the 5d close sitting 1% beyond the
    -- signal-day close). The previous adaptive k_n · σ(code, stat_month,
    -- n) bar ("std" mode; k_n = {next: 0.5, 5d: 0.75, 20d: 1.0,
    -- 60d: 1.0}) is disabled in the forecasts config.
    reverse_threshold   NUMERIC(8,6) NOT NULL DEFAULT 0.01,

    CONSTRAINT pk_forecast_results PRIMARY KEY (forecast_id, period)
) PARTITION BY HASH (forecast_id);

-- Native hash partitions (16) keyed by forecast_id — all 4 periods of
-- the same forecast land in the same partition, so partition pruning
-- still works fine on forecast_id filters. Created via the shared util
-- (database/sql/00_partition_utils.sql); children named _p00.._p15
SELECT public.create_hash_partitions('analysis_forecasts', 'forecast_results', 16);

-- ----------------------------------------------------------------------------
--  Idempotent migration (pre-existing installs) — ADD COLUMN propagates
--  to all hash partitions; pre-existing rows keep the legacy fixed 1%
--  bar they were computed at (0.01 = the column default). The adaptive
--  values arrive when the forecasts run is rebuilt (--force).
-- ----------------------------------------------------------------------------
ALTER TABLE analysis_forecasts.forecast_results
    ADD COLUMN IF NOT EXISTS reverse_threshold NUMERIC(8,6) NOT NULL DEFAULT 0.01;

ALTER TABLE analysis_forecasts.forecast_results
    ADD COLUMN IF NOT EXISTS lookback_period TEXT NOT NULL DEFAULT '5y';

ALTER TABLE analysis_forecasts.forecast_results
    ADD COLUMN IF NOT EXISTS trigger_dates DATE[];

ALTER TABLE analysis_forecasts.forecast_results
    ADD COLUMN IF NOT EXISTS streak_starts DATE[];

ALTER TABLE analysis_forecasts.forecast_results
    ADD COLUMN IF NOT EXISTS streak_ends DATE[];

ALTER TABLE analysis_forecasts.forecast_results
    ADD COLUMN IF NOT EXISTS streak_days BIGINT[];

ALTER TABLE analysis_forecasts.forecast_results
    ADD COLUMN IF NOT EXISTS trigger_excess NUMERIC(10,6)[];

-- ----------------------------------------------------------------------------
--  Comments
-- ----------------------------------------------------------------------------
COMMENT ON TABLE analysis_forecasts.forecast_results IS 'Normalized forecast RESULT data. One row per (forecast_id, period) — each forecast bucket from mov_rsi/mov_std has up to 4 rows (period: next/5d/20d/60d). Carries the mean / std-dev / max / min forward fractional changes (max/min NULL for period=next), per-period occurrence count, within-window close swing amplitude (max_low_change_ratio, NULL for period=next), and per-period reversal probabilities computed against the row''s reverse_threshold — the FIXED 1% bar: P(the period-end n-day close change is a reversal beyond ±1%; e.g. the 5d probability is about the 5d close) — see reverse_threshold. config JSONB carries per-bucket motivation data duplicated across all 4 period rows of the same forecast_id. Partitioned by HASH(forecast_id). Populated by python -m analyze.analysis_forecasts.';
COMMENT ON COLUMN analysis_forecasts.forecast_results.forecast_id IS 'Surrogate identity PK. Allocated by the writer (python -m analyze.analysis_forecasts) and mirrored into the motivation row of analysis_forecasts.mov_rsi / mov_std.';
COMMENT ON COLUMN analysis_forecasts.forecast_results.period IS 'Forward horizon period: ''next'' (next-day), ''5d'' (5 trading days), ''20d'' (20 trading days), ''60d'' (60 trading days). PK member.';
COMMENT ON COLUMN analysis_forecasts.forecast_results.config IS 'JSONB config for per-bucket motivation data that varies by analysis type. Duplicated across all 4 period rows of the same forecast_id. px_vol rows store {"mean_t": float, "mean_z": float}; margin_ratio rows store {"mean_ratio": float|null, "mean_z": float|null}. mov_rsi / mov_std / mov_gap rows are NULL (no config data). All fractional: 0.012 = 1.2%.';
COMMENT ON COLUMN analysis_forecasts.forecast_results.ave_change IS 'Mean n-trading-day forward fractional change (close[t+n]-close[t])/close[t] over bucket days with a valid n-day forward change. NULL when none.';
COMMENT ON COLUMN analysis_forecasts.forecast_results.std_change IS 'Population standard deviation of the n-trading-day forward fractional change over the SAME bucket days as ave_change (dispersion of the horizon outcomes: sqrt(E[x²] − E[x]²)). NULL when no valid days (occurrence_count = 0).';
COMMENT ON COLUMN analysis_forecasts.forecast_results.max_change IS 'Maximum n-trading-day forward fractional change (close-based) over bucket days with a valid n-day forward change. NULL for period=''next'' (no 1-day max/min) or when none.';
COMMENT ON COLUMN analysis_forecasts.forecast_results.min_change IS 'Minimum n-trading-day forward fractional change (close-based) over bucket days with a valid n-day forward change. NULL for period=''next'' (no 1-day max/min) or when none.';
COMMENT ON COLUMN analysis_forecasts.forecast_results.occurrence_count IS 'Number of bucket days with a valid n-trading-day forward change — the denominator of ave_change / reverse_prob.';
COMMENT ON COLUMN analysis_forecasts.forecast_results.trigger_dates IS 'Calendar DATEs (ascending) of the bucket days with a valid n-trading-day forward change — the SAME days as occurrence_count (array length == occurrence_count; NULL when the count is 0/NULL). Under the 2026-09 streak-merge these are the merged signals'' MID days (one per run of continuously-qualifying days). The exact dates the row''s ave_change / std_change / max / min / reverse_prob were computed over; the UI marks them on the code''s price trend when a forecast row is clicked. Row-local per period: later horizons (20d/60d) drop trailing bucket days whose forward window was not yet complete.';
COMMENT ON COLUMN analysis_forecasts.forecast_results.streak_starts IS 'Calendar DATEs element-wise parallel to trigger_dates: each merged signal''s qualifying-run START date (the first day of the consecutive run the mid was anchored at — mid_date - ((run_len-1)//2) grid rows). NULL arrays for the state families (px_vol_state / margin_ratio_state / opp_pair_state — every qualifying day is its own 1-day signal, so the span equals the mid date) and for rows written before the 2026-09 streak-span migration; row-local per period like trigger_dates. Drives the UI''s dark-purple streak-period shading.';
COMMENT ON COLUMN analysis_forecasts.forecast_results.streak_ends IS 'Calendar DATEs element-wise parallel to trigger_dates: each merged signal''s qualifying-run END date (the last consecutive day — mid_date + (run_len//2) grid rows). NULL arrays for the state families and pre-migration rows; row-local per period like trigger_dates. Drives the UI''s dark-purple streak-period shading.';
COMMENT ON COLUMN analysis_forecasts.forecast_results.streak_days IS 'TRADING-day counts element-wise parallel to trigger_dates: each merged signal''s qualifying-run length — the number of consecutive grid days the run merges (the same run_len that anchored the mid at mid_date - ((run_len-1)//2) rows and spans streak_starts[r] .. streak_ends[r], and whose per-bucket mean is forecast_identities.streak_signal_days). NULL arrays for the state families (every qualifying day is its own 1-day signal) and pre-migration rows; row-local per period like trigger_dates.';
COMMENT ON COLUMN analysis_forecasts.forecast_results.trigger_excess IS 'Per-signal TRIGGER EXCESS, element-wise parallel to trigger_dates: the mid day''s trigger value minus the bucket''s qualifying bar (signed, value − bar — the live_signals signal_excess convention: positive beyond an upper/top bar, negative below a lower/bottom one; the pairs families'' bar is the zero line, so there the excess IS the day''s spread). Units are each family''s own signal scale: RSI / gap indicator points minus their percentile bar for mov_rsi / mov_gap, the price minus the breached MA ± k·σ band edge for mov_std, the spread itself for mov_pairs / mov_pairs_ema. Written by the scalar-bar event engines only (mov_rsi / mov_std / mov_gap / mov_pairs / mov_pairs_ema); the state families (px_vol_state / margin_ratio_state / opp_pair_state / pe_state / dividend_state — band membership, no scalar bar) and high_low_streaks (ex-post streak anchors, not scalar breaches) carry NULL arrays. Array length == occurrence_count (NULL when the count is 0/NULL); row-local per period like trigger_dates — gathered over the same valid forward-window days, so it pairs element-wise with the trigger_dates / streak_* arrays.';
COMMENT ON COLUMN analysis_forecasts.forecast_results.max_low_change_ratio IS '(1 + max_change) / (1 + min_change) = max(close[t+1..t+n]) / min(close[t+1..t+n]) — the within-window close swing amplitude derived from the row''s max/min forward changes. NULL for period=''next'' or when no valid days (or min_change <= -1).';
COMMENT ON COLUMN analysis_forecasts.forecast_results.reverse_prob IS 'Probability that a bucket day (with valid n-day change) REVERSES beyond the row''s reverse_threshold against the bucket side: n-day change < −reverse_threshold for top/upper buckets, > +reverse_threshold for bottom/lower buckets.';
COMMENT ON COLUMN analysis_forecasts.forecast_results.lookback_period IS 'Trailing calendar window the bucket was computed over (recorded build parameter; duplicated across all 4 period rows of the same forecast_id): ''5y'' = the window (stat_month - 5 years, stat_month] of the code''s own trading days. Default ''5y''; a rebuild with a different lookback requires --force.';
COMMENT ON COLUMN analysis_forecasts.forecast_results.reverse_threshold IS 'The fractional reversal bar this row''s reverse_prob was computed against: the FIXED 1% (0.01) bar since 2026-09-08 — reverse_prob = P(the PERIOD-END n-day close change is a reversal beyond ±1%; the 5d probability is about the 5d close vs the signal-day close). The previous adaptive k_n · σ bar (temp_scripts/study_reverse_threshold.py) is disabled — REVERSE_THRESHOLD_MODE = "fixed" in the forecasts config.';

-- ============================================================================
--  Table: analysis_forecasts.forecast_identities
--
--  Shared-PK REGISTRY of every forecast bucket — the single place a
--  forecast_id resolves to its identity. The motivation tables (mov_rsi /
--  mov_std / mov_gap / mov_pairs / mov_pairs_ema / px_vol_state /
--  margin_ratio_state / opp_pair_state / high_low_streaks) each repeat
--  the SAME leading PK columns (sec_type, code, stat_month) alongside
--  their family-specific bucket axes; forecast_identities migrates that
--  shared PK into ONE table keyed by the surrogate forecast_id, so a
--  search by forecast_id (UI / API / CLI) lands directly on the
--  security, month and bucket FAMILY without probing all nine tables.
--
--  One row per forecast bucket (1:1 with each motivation row and its 4
--  forecast_results period rows; forecast_id values are allocated from
--  the SAME sequence as forecast_results.forecast_id —
--  analysis_forecasts.forecast_results_forecast_id_seq — by the writer,
--  which COPYs the identity row in the same transaction).
--
--  Columns:
--    forecast_id       — PK; the surrogate shared with forecast_results
--                        and the motivation tables.
--    sec_type          — 'etf' | 'index' | 'stock' (opp_pair rows carry
--                        the gate-machinery constant 'index').
--    code              — the forecast SUBJECT: the security ticker for
--                        the mov_* / *_state / streak families; the
--                        DROPPING (trigger) industry_id for opp_pair_state
--                        rows (the forecast-target pair_industry_id stays
--                        on the opp_pair_state motivation row — pair_code
--                        was dropped as corr-forecasts-only).
--    stat_month        — completed month-end DATE of the snapshot.
--    bucket            — the motivation TABLE name ('mov_rsi', ...,
--                        'opp_pair_state') — which family's axes refine
--                        this identity (join that table on forecast_id).
--    streak_signal_days — the bucket's mean streak length per SIGNAL
--                        (the 2026-09 streak-merge semantics: event
--                        families merge runs of continuously-qualifying
--                        days into ONE mid-anchored signal, so this is
--                        the MEAN run length over the bucket's merged
--                        signals; state families admit every qualifying
--                        day — a 1-day signal — and record 1;
--                        high_low_streaks records its config
--                        mean_day_count). NUMERIC(10,2).
--    lookback_period   — recorded build parameter, as on every other
--                        analysis_forecasts table ('5y').
--
--  base_rates has NO forecast_id (it is the unconditional reference, not
--  a bucket) and is therefore NOT part of the registry.
--
--  Population:
--    - writer: python -m analyze.analysis_forecasts COPYs one identity
--      row per bucket alongside the motivation + result rows, and
--      deletes the registry rows of every deleted forecast_id
--      (refresh / --force);
--    - backfill below: idempotent INSERT ... ON CONFLICT DO NOTHING
--      from every existing motivation table (guarded per table — fresh
--      installs apply 01_ before the motivation tables exist, in which
--      case the backfill is a no-op and the writer populates the
--      registry from its first run). Pre-existing rows keep the column
--      default streak_signal_days = 1; the per-bucket mean streak
--      lengths arrive with the --force rebuild (the migration precedent
--      of forecast_results.reverse_threshold).
-- ============================================================================

CREATE TABLE IF NOT EXISTS analysis_forecasts.forecast_identities (
    forecast_id     BIGINT         NOT NULL,  -- surrogate id (forecast_results' sequence)
    sec_type        TEXT           NOT NULL,  -- 'etf' | 'index' | 'stock' (opp_pair: constant 'index')
    code            TEXT           NOT NULL,  -- security ticker; opp_pair: the DROPPING industry_id
    stat_month      DATE           NOT NULL,  -- completed month-end (trailing 5y window end)
    bucket          TEXT           NOT NULL,  -- motivation table name refining this identity
    streak_signal_days NUMERIC(10,2) NOT NULL DEFAULT 1,  -- mean run length per merged signal

    -- recorded build parameter (NOT a key member), as on every table
    lookback_period TEXT           NOT NULL DEFAULT '5y',

    CONSTRAINT pk_forecast_identities PRIMARY KEY (code, forecast_id)
) PARTITION BY HASH (code);

-- Native hash partitions (16) keyed by code — the code-clustered
-- read/write axis: a per-security search prunes to ONE partition and
-- walks the code-leading PK. forecast_id-only lookups (the
-- search-by-forecast_id CLI / UI identity endpoint) are served by the
-- secondary idx_forecast_identities_forecast_id below
-- (database/sql/00_partition_utils.sql; children _p00.._p15).
SELECT public.create_hash_partitions('analysis_forecasts', 'forecast_identities', 16);

-- ----------------------------------------------------------------------------
--  Migration (2026-09): (code, forecast_id) PK on HASH (code) partitions.
--  forecast_identities always carried code, so rows copy over directly.
--  Guarded by shape detection — no-op on fresh installs (created above
--  in the target shape) and after the migration.
-- ----------------------------------------------------------------------------
DO $$
DECLARE
    r         int;
    v_partkey text;
    v_pkdef   text;
BEGIN
    SELECT pg_get_partkeydef(c.oid),
           COALESCE((SELECT pg_get_constraintdef(p.oid)
                     FROM pg_constraint p
                     WHERE p.conrelid = c.oid AND p.contype = 'p'), '')
    INTO v_partkey, v_pkdef
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'analysis_forecasts'
      AND c.relname = 'forecast_identities';
    IF v_partkey IS NULL
       OR (v_partkey = 'HASH (code)'
           AND v_pkdef = 'PRIMARY KEY (code, forecast_id)') THEN
        RETURN;
    END IF;
    ALTER TABLE analysis_forecasts.forecast_identities
        RENAME TO forecast_identities_pk_rebuild;
    ALTER TABLE analysis_forecasts.forecast_identities_pk_rebuild
        DROP CONSTRAINT IF EXISTS pk_forecast_identities;
    CREATE TABLE analysis_forecasts.forecast_identities_new (
        forecast_id     BIGINT         NOT NULL,
        sec_type        TEXT           NOT NULL,
        code            TEXT           NOT NULL,
        stat_month      DATE           NOT NULL,
        bucket          TEXT           NOT NULL,
        streak_signal_days NUMERIC(10,2) NOT NULL DEFAULT 1,
        lookback_period TEXT           NOT NULL DEFAULT '5y',
        CONSTRAINT pk_forecast_identities PRIMARY KEY (code, forecast_id)
    ) PARTITION BY HASH (code);
    PERFORM public.create_hash_partitions('analysis_forecasts',
                                          'forecast_identities_new', 16);
    ALTER TABLE analysis_forecasts.forecast_identities_pk_rebuild
        ADD COLUMN IF NOT EXISTS streak_signal_days NUMERIC(10,2) NOT NULL DEFAULT 1;
    ALTER TABLE analysis_forecasts.forecast_identities_pk_rebuild
        ADD COLUMN IF NOT EXISTS lookback_period TEXT NOT NULL DEFAULT '5y';
    INSERT INTO analysis_forecasts.forecast_identities_new
           (forecast_id, sec_type, code, stat_month, bucket,
            streak_signal_days, lookback_period)
    SELECT  forecast_id, sec_type, code, stat_month, bucket,
            streak_signal_days, lookback_period
    FROM    analysis_forecasts.forecast_identities_pk_rebuild;
    DROP TABLE analysis_forecasts.forecast_identities_pk_rebuild;
    ALTER TABLE analysis_forecasts.forecast_identities_new
        RENAME TO forecast_identities;
    FOR r IN 0..15 LOOP
        EXECUTE format(
            'ALTER TABLE analysis_forecasts.forecast_identities_new_p%s '
            'RENAME TO forecast_identities_p%s',
            lpad(r::text, 2, '0'), lpad(r::text, 2, '0'));
    END LOOP;
END $$;

-- Identity search indexes — created AFTER the migration swap so they
-- land on the final table. The motivation tables carry code alone as
-- their partition key, so forecast_identities is the ONLY place
-- (sec_type, stat_month) exists and carries every search path:
--   _code_bucket — per-security + per-family lookups (the UI's
--     forecast-table endpoint: months dropdown + bucket rows of one
--     (code, bucket) pair, stat_month-ordered);
--   _bucket — per-family month scans (the writer's incremental month
--     detection + refresh deletes, the signals layer's present-month
--     detection, the gate's sec_type+bucket+month<=X scans);
--   _forecast_id — the search-by-forecast_id lookups (the PK now leads
--     with code, so an id-only predicate needs its own index).
DROP INDEX IF EXISTS analysis_forecasts.idx_forecast_identities_code;

CREATE INDEX IF NOT EXISTS idx_forecast_identities_code_bucket
    ON analysis_forecasts.forecast_identities (sec_type, code, bucket, stat_month DESC);

CREATE INDEX IF NOT EXISTS idx_forecast_identities_bucket
    ON analysis_forecasts.forecast_identities (sec_type, bucket, stat_month);

CREATE INDEX IF NOT EXISTS idx_forecast_identities_forecast_id
    ON analysis_forecasts.forecast_identities (forecast_id);

-- ----------------------------------------------------------------------------
--  Idempotent migration (pre-existing installs): ADD COLUMN propagates
--  to all hash partitions; pre-existing rows keep the column default 1
--  (the per-bucket mean streak lengths arrive with the --force
--  rebuild). pair_code was corr-forecasts-only (opp_pair_state) — the
--  forecast-target pair_industry_id stays on that motivation row.
-- ----------------------------------------------------------------------------
ALTER TABLE analysis_forecasts.forecast_identities
    ADD COLUMN IF NOT EXISTS streak_signal_days NUMERIC(10,2) NOT NULL DEFAULT 1;

ALTER TABLE analysis_forecasts.forecast_identities
    DROP COLUMN IF EXISTS pair_code;


-- ----------------------------------------------------------------------------
--  Comments
-- ----------------------------------------------------------------------------
COMMENT ON TABLE analysis_forecasts.forecast_identities IS 'Identity registry of every forecast bucket: one row per forecast_id holding the shared identity (sec_type, code, stat_month) — the motivation tables store code alone as their partition key, so this is the ONLY table with the full identity — plus the bucket family (the motivation table name — join it on forecast_id for the family-specific axes), the bucket''s mean streak length per merged signal (streak_signal_days) and the recorded lookback_period. 1:1 with each motivation row and its 4 forecast_results period rows; forecast_ids come from the same sequence (forecast_results_forecast_id_seq). code = the forecast subject (security ticker; the DROPPING industry_id for opp_pair_state rows — the forecast-target pair_industry_id lives on that motivation row). PK (code, forecast_id) on HASH (code) partitions — per-security searches prune to one partition; id-only lookups use idx_forecast_identities_forecast_id. Powers search by forecast_id (API / UI / python -m analyze.analysis_forecasts --search-forecast-id). Populated by the writer (same transaction as the motivation + result rows) and by the idempotent backfill in this file; base_rates is NOT in the registry (no forecast_id).';
COMMENT ON COLUMN analysis_forecasts.forecast_identities.forecast_id IS 'Surrogate PK, allocated from the shared sequence analysis_forecasts.forecast_results_forecast_id_seq by the writer — the same id as the bucket''s motivation-table row and its 4 forecast_results period rows.';
COMMENT ON COLUMN analysis_forecasts.forecast_identities.sec_type IS 'Security type: etf (ETF), index (CSI-style index), or stock (individual equity). opp_pair_state rows carry the gate-machinery constant ''index'' (industry_id codes are type=''index'' classification members).';
COMMENT ON COLUMN analysis_forecasts.forecast_identities.code IS 'The forecast SUBJECT: the security ticker for the mov_* / px_vol_state / margin_ratio_state / high_low_streaks families (ETF suffix e.g. "510050.SS"; index bare e.g. "000300"); for opp_pair_state rows the DROPPING industry_id (the trigger side of the pair — the forecast-target pair_industry_id stays on the opp_pair_state motivation row).';
COMMENT ON COLUMN analysis_forecasts.forecast_identities.stat_month IS 'Completed month-end date. The bucket was computed over the trailing 5-year window (stat_month - 5 years, stat_month] of the code''s own trading days.';
COMMENT ON COLUMN analysis_forecasts.forecast_identities.bucket IS 'The motivation TABLE name whose axes refine this identity: mov_rsi / mov_std / mov_gap / mov_pairs / mov_pairs_ema / px_vol_state / margin_ratio_state / high_low_streaks / opp_pair_state. Join analysis_forecasts.<bucket> ON forecast_id for the family-specific bucket config (window / side / pct / k / states / streak band / pair trend_window).';
COMMENT ON COLUMN analysis_forecasts.forecast_identities.streak_signal_days IS 'The bucket''s mean streak length per SIGNAL (fractional, 2dp): the 2026-09 streak-merge merged runs of continuously-qualifying days into ONE forecast signal anchored at each run''s MID day, so the event families (mov_rsi / mov_std / mov_gap / mov_pairs / mov_pairs_ema) record the MEAN run length over the bucket''s merged signals; the state families (px_vol_state / margin_ratio_state / opp_pair_state) admit every qualifying day — a 1-day signal — and record 1; high_low_streaks records its config mean_day_count. Column default 1 (pre-streak backfill rows; real means arrive with the --force rebuild).';
COMMENT ON COLUMN analysis_forecasts.forecast_identities.lookback_period IS 'Recorded build parameter (NOT a key member): the trailing calendar window the bucket was computed over — ''5y'' = (stat_month - 5 years, stat_month]. Mirrors the same column on the motivation and result tables.';
