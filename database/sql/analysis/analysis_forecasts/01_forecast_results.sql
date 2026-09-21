-- ============================================================================
--  Tables: analysis_forecasts.forecast_results
--          + analysis_forecasts.forecast_identities (bottom of this file)
--
--  Normalized RESULT data of the forecast analysis. One row per
--  (forecast_id, delay, period) — each forecast bucket has up to 24
--  rows: one row per emitted ANCHOR DELAY (delay 0..5 — the trigger's
--  trading-day offset within its qualifying streak; 0 = the streak's
--  first qualifying day, the moment the signal becomes observable) ×
--  4 periods (the three forward horizons period ∈ {'next', '5d',
--  '20d'} plus the weight-blended 'mixed' row — the FIXED-weight blend
--  of the three horizons at 5d 0.65 / next 0.25 / 20d 0.10 — see the
--  backfill below; the analysis_signals gate reads the delay-0 row so
--  every forecast horizon of the same fresh signal trigger contributes
--  to the signal). A persistent streak emits one trigger per day
--  0..min(run_len - 1, 5), so each delay row's forward stats are
--  conditioned on the signal having lasted that long — a live streak
--  can be re-checked one day at a time as it extends. Rows exist only
--  for the (bucket, delay) pairs the bucket's streaks actually
--  reached. The config JSONB is per-forecast (not per-period/delay),
--  duplicated across all of a forecast_id's rows.
--
--  forecast_id links 1:1 from analysis_forecasts.mov_rsi / mov_std;
--  PK is (forecast_id, delay, period) so the JOIN naturally expands
--  1→4·D.
--  forecast_identities (shared-PK registry, defined at the bottom of
--  this file) resolves each forecast_id to (sec_type, code, stat_month,
--  bucket family) — the identity every motivation table repeats in its
--  leading PK — so a search by forecast_id needs no table probing.
--
--  Columns (consolidated — no period suffix in column name; the
--  ``period`` column carries that role):
--    period           — 'next' (next day), '5d' / '20d',
--                       or 'mixed' (the weight-blended forward profile)
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
--                       row-local: the 20d row lists fewer dates than the
--                       next row when the bucket's trailing days lack a
--                       20-day forward window.
--    trigger_excess   — NUMERIC(10,6)[] element-wise parallel to
--                       trigger_dates: each merged signal's TRIGGER
--                       EXCESS — the anchor day's trigger value minus the
--                       bucket's qualifying bar (signed, value − bar;
--                       the live_signals signal_excess convention).
--                       Written by the scalar-bar event engines only
--                       (mov_rsi: indicator − percentile bar;
--                       mov_std: price − breached band edge; mov_pairs /
--                       mov_pairs_ema: the day's spread, the bar being
--                       the zero line); NULL arrays for the state
--                       families (px_vol_state / margin_ratio_state /
--                       opp_pair_state / pe_state / dividend_state —
--                       band membership, no scalar bar) and
--                       high_low_streaks (ex-post streak anchors).
--    reverse_prob     — P(n-day change is a REVERSAL beyond the row's
--                       threshold against the bucket side),
--                       over bucket days with a valid n-day forward
--                       change
--    threshold — the reversal bar reverse_prob was computed
--                       against (fractional). The FIXED 1% bar (0.01)
--                       since 2026-09-08 — reverse_prob reads as a
--                       plain "P > 1%" on the period-end n-day close.
--                       The earlier adaptive bar (k_n · σ(code,
--                       stat_month, n), k_n = {next: 0.5, 5d: 0.75,
--                       20d: 1.0} — the 2026-09 std-mode
--                       study) is DISABLED in the forecasts config
--                       (REVERSE_THRESHOLD_MODE = "fixed"), kept only
--                       as a flip-back option; rows written under it
--                       carry their computed k_n·σ value until the
--                       next refresh/--force rewrite.
--
--  High/low are NULL when the denominator is 0.
-- ============================================================================

CREATE TABLE IF NOT EXISTS analysis_forecasts.forecast_results (
    forecast_id         BIGINT GENERATED BY DEFAULT AS IDENTITY,

    -- Period of the forward horizon — one of 'next' / '5d' / '20d'.
    period              TEXT NOT NULL,

    -- ANCHOR DELAY (PK member): the trigger's trading-day offset
    -- within its qualifying streak — 0 = the streak's first
    -- qualifying day (the moment the signal becomes observable), up
    -- to 5. A persistent streak contributes one trigger per day
    -- 0..min(run_len - 1, 5), so the delay-d row's forward stats are
    -- conditioned on the signal having lasted d + 1 days. 1-day /
    -- state-family signals emit delay 0 only.
    delay               SMALLINT NOT NULL DEFAULT 0,

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
    --   mov_rsi / mov_std: NULL (no config data)
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
    -- trigger_dates: the anchor day's trigger value minus the bucket's
    -- qualifying bar (signed, value − bar — the live_signals
    -- .signal_excess convention: positive beyond an upper/top bar,
    -- negative below a lower/bottom one; the pairs families' bar is
    -- the zero line, so there the excess IS the day's spread). Only
    -- the scalar-bar engines write it (mov_rsi:
    -- indicator − percentile bar; mov_std: price − breached band
    -- edge; mov_pairs / mov_pairs_ema: the spread; pe_state /
    -- dividend_state: the PE / dividend-yield minus the bucket's
    -- quantile bar); the state families (px_vol_state /
    -- margin_ratio_state / opp_pair_state — band membership, no
    -- scalar bar) and high_low_streaks (ex-post streak anchors) carry
    -- NULL arrays. Length == occurrence_count; NULL when 0/NULL;
    -- row-local per period like trigger_dates.
    trigger_excess      NUMERIC(10,6)[],

    -- P(forward change reverses beyond threshold against the
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
    -- n) bar ("std" mode; k_n = {next: 0.5, 5d: 0.75, 20d: 1.0})
    -- is disabled in the forecasts config.
    threshold   NUMERIC(8,6) NOT NULL DEFAULT 0.01,

    CONSTRAINT pk_forecast_results PRIMARY KEY (forecast_id, period)
) PARTITION BY HASH (forecast_id);

-- Native hash partitions (16) keyed on forecast_id — all 4 periods of
-- the same forecast land in the same partition, so partition pruning
-- still works fine on forecast_id filters. Created via the shared util
-- (database/sql/00_partition_utils.sql); children named _p00.._p15
SELECT public.create_hash_partitions('analysis_forecasts', 'forecast_results', 16);

-- 2026-09 ratio removal: max_low_change_ratio (the within-window close
-- swing amplitude) is retired — dropped here so existing installations
-- lose the column; fresh installs never create it. The UI's Recent
-- Movements forecast table no longer surfaces it.
ALTER TABLE analysis_forecasts.forecast_results
    DROP COLUMN IF EXISTS max_low_change_ratio;

-- ----------------------------------------------------------------------------
--  Comments
-- ----------------------------------------------------------------------------
COMMENT ON TABLE analysis_forecasts.forecast_results IS 'Normalized forecast RESULT data. One row per (forecast_id, delay, period) — each forecast bucket has up to 24 rows: one per emitted ANCHOR DELAY (delay 0..5 — the trigger''s trading-day offset within its qualifying streak; 0 = the streak''s first qualifying day, the moment the signal becomes observable; a persistent streak emits one trigger per day 0..min(run_len-1, 5), so the delay-d rows'' forward stats are conditioned on the signal having lasted d+1 days) × the four periods: the three horizons (period: next/5d/20d) plus the weight-blended mixed row (5d 0.65 / next 0.25 / 20d 0.10 — the whole forward profile of one trigger as ONE row; the analysis_signals emit gate reads the delay-0 row). Rows exist only for the (bucket, delay) pairs the bucket''s streaks actually reached. Carries the mean / std-dev / max / min forward fractional changes (max/min NULL for period=next and NULL on the mixed row — extrema do not blend), per-period occurrence count, and per-period reversal probabilities computed against the row''s threshold — the FIXED 1% bar — see threshold. config JSONB carries per-bucket motivation data duplicated across all period rows of the same forecast_id. Partitioned by HASH(forecast_id). Populated by python -m analyze.analysis_forecasts.';
COMMENT ON COLUMN analysis_forecasts.forecast_results.forecast_id IS 'Surrogate identity PK. Allocated by the writer (python -m analyze.analysis_forecasts) and mirrored into the motivation row of analysis_forecasts.mov_rsi / mov_std.';
COMMENT ON COLUMN analysis_forecasts.forecast_results.period IS 'Forward horizon period: ''next'' (next-day), ''5d'' (5 trading days), ''20d'' (20 trading days), or ''mixed'' — the FIXED-weight blend of the three horizon rows (5d 0.65 / next 0.25 / 20d 0.10; weights renormalized over the horizons whose stats exist, occurrence_count = the MIN valid count over those legs, threshold = the full-weight mean of the three bars, max/min/date/excess arrays NULL — they do not blend). The analysis_signals emit gate consumes the delay-0 mixed row. PK member.';
COMMENT ON COLUMN analysis_forecasts.forecast_results.delay IS 'The ANCHOR DELAY this row''s forecast is measured from — the trigger''s trading-day offset within its qualifying streak: 0 = the streak''s first qualifying day (the moment the signal becomes observable), up to 5. Under the 2026-09-21 incremental-anchor migration a persistent streak emits one trigger per day 0..min(run_len - 1, 5) — each delay row''s forward stats are conditioned on the signal having lasted delay + 1 days, so a live streak can be re-checked one day at a time as it extends. 1-day / state-family signals emit delay 0 only. PK member; rows exist only for the (bucket, delay) pairs the bucket''s streaks actually reached.';
COMMENT ON COLUMN analysis_forecasts.forecast_results.config IS 'JSONB config for per-bucket motivation data that varies by analysis type. Duplicated across all period rows of the same forecast_id. px_vol rows store {"mean_t": float, "mean_z": float}; margin_ratio rows store {"mean_ratio": float|null, "mean_z": float|null}. mov_rsi / mov_std rows are NULL (no config data). All fractional: 0.012 = 1.2%.';
COMMENT ON COLUMN analysis_forecasts.forecast_results.ave_change IS 'Mean n-trading-day forward fractional change (close[t+n]-close[t])/close[t] over bucket days with a valid n-day forward change. NULL when none.';
COMMENT ON COLUMN analysis_forecasts.forecast_results.std_change IS 'Population standard deviation of the n-trading-day forward fractional change over the SAME bucket days as ave_change (dispersion of the horizon outcomes: sqrt(E[x²] − E[x]²)). NULL when no valid days (occurrence_count = 0).';
COMMENT ON COLUMN analysis_forecasts.forecast_results.max_change IS 'Maximum n-trading-day forward fractional change (close-based) over bucket days with a valid n-day forward change. NULL for period=''next'' (no 1-day max/min) or when none.';
COMMENT ON COLUMN analysis_forecasts.forecast_results.min_change IS 'Minimum n-trading-day forward fractional change (close-based) over bucket days with a valid n-day forward change. NULL for period=''next'' (no 1-day max/min) or when none.';
COMMENT ON COLUMN analysis_forecasts.forecast_results.occurrence_count IS 'Number of bucket days with a valid n-trading-day forward change — the denominator of ave_change / reverse_prob.';
COMMENT ON COLUMN analysis_forecasts.forecast_results.trigger_dates IS 'Calendar DATEs (ascending) of the bucket days with a valid n-trading-day forward change — the SAME days as occurrence_count (array length == occurrence_count; NULL when the count is 0/NULL). Under the 2026-09 streak-merge + 2026-09-21 incremental anchors these are the emitted trigger anchors — one per day 0..min(run_len-1, 5) of each run of continuously-qualifying days (delay 0 = the run''s first day). The exact dates the row''s ave_change / std_change / max / min / reverse_prob were computed over; the UI marks them on the code''s price trend when a forecast row is clicked. Row-local per period: later horizons (20d) drop trailing bucket days whose forward window was not yet complete.';
COMMENT ON COLUMN analysis_forecasts.forecast_results.streak_starts IS 'Calendar DATEs element-wise parallel to trigger_dates: each merged signal''s qualifying-run START date (the first day of the consecutive run the row''s anchors belong to — the delay-0 anchor date). NULL arrays for the state families (px_vol_state / margin_ratio_state / opp_pair_state — every qualifying day is its own 1-day signal, so the span equals the mid date) and for rows written before the 2026-09 streak-span migration; row-local per period like trigger_dates. Drives the UI''s dark-purple streak-period shading.';
COMMENT ON COLUMN analysis_forecasts.forecast_results.streak_ends IS 'Calendar DATEs element-wise parallel to trigger_dates: each merged signal''s qualifying-run END date (the last consecutive qualifying day of the run). NULL arrays for the state families and pre-migration rows; row-local per period like trigger_dates. Drives the UI''s dark-purple streak-period shading.';
COMMENT ON COLUMN analysis_forecasts.forecast_results.streak_days IS 'TRADING-day counts element-wise parallel to trigger_dates: each merged signal''s qualifying-run length — the number of consecutive grid days the run merges (the run that the row''s anchors span streak_starts[r] .. streak_ends[r] belong to, and whose per-bucket mean is forecast_identities.streak_signal_days). NULL arrays for the state families (every qualifying day is its own 1-day signal) and pre-migration rows; row-local per period like trigger_dates.';
COMMENT ON COLUMN analysis_forecasts.forecast_results.trigger_excess IS 'Per-signal TRIGGER EXCESS, element-wise parallel to trigger_dates: the ANCHOR day''s trigger value minus the bucket''s qualifying bar (signed, value − bar — the live_signals signal_excess convention: positive beyond an upper/top bar, negative below a lower/bottom one; the pairs families'' bar is the zero line, so there the excess IS the day''s spread). Units are each family''s own signal scale: RSI indicator points minus their percentile bar for mov_rsi, the price minus the breached MA ± k·σ band edge for mov_std, the spread itself for mov_pairs / mov_pairs_ema, the PE / dividend-yield minus the bucket''s quantile bar for pe_state / dividend_state. Written by the scalar-bar engines only (mov_rsi / mov_std / mov_pairs / mov_pairs_ema / pe_state / dividend_state); the state families (px_vol_state / margin_ratio_state / opp_pair_state — band membership, no scalar bar) and high_low_streaks (ex-post streak anchors, not scalar breaches) carry NULL arrays. Array length == occurrence_count (NULL when the count is 0/NULL); row-local per period like trigger_dates — gathered over the same valid forward-window days, so it pairs element-wise with the trigger_dates / streak_* arrays.';
COMMENT ON COLUMN analysis_forecasts.forecast_results.reverse_prob IS 'Probability that a bucket day (with valid n-day change) REVERSES beyond the row''s threshold against the bucket side: n-day change < −threshold for top/upper buckets, > +threshold for bottom/lower buckets.';
COMMENT ON COLUMN analysis_forecasts.forecast_results.lookback_period IS 'Trailing calendar window the bucket was computed over (recorded build parameter; duplicated across all period rows of the same forecast_id): ''5y'' = the window (stat_month - 5 years, stat_month] of the code''s own trading days. Default ''5y''; a rebuild with a different lookback requires --force.';
COMMENT ON COLUMN analysis_forecasts.forecast_results.threshold IS 'The fractional reversal bar this row''s reverse_prob was computed against: the FIXED 1% (0.01) bar since 2026-09-08 — reverse_prob = P(the PERIOD-END n-day close change is a reversal beyond ±1%; the 5d probability is about the 5d close vs the signal-day close). The previous adaptive k_n · σ bar (temp_scripts/study_threshold.py) is disabled — REVERSE_THRESHOLD_MODE = "fixed" in the forecasts config. On the mixed row: the full-weight mean of the three horizons'' bars (exactly 0.01 under the fixed bar).';


-- ============================================================================
--  Table: analysis_forecasts.forecast_identities
--
--  Shared-PK REGISTRY of every forecast bucket — the single place a
--  forecast_id resolves to its identity. The motivation tables (mov_rsi /
--  mov_std / mov_pairs / mov_pairs_ema / px_vol_state /
--  margin_ratio_state / opp_pair_state / high_low_streaks) each repeat
--  the SAME leading PK columns (sec_type, code, stat_month) alongside
--  their family-specific bucket axes; forecast_identities migrates that
--  shared PK into ONE table keyed by the surrogate forecast_id, so a
--  search by forecast_id (UI / API / CLI) lands directly on the
--  security, month and bucket FAMILY without probing all nine tables.
--
--  One row per forecast bucket (1:1 with each motivation row and its
--  forecast_results period × delay rows; forecast_id values are allocated from
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
--                        signals — the number of days a signal is
--                        ACTIVE; state families admit every qualifying
--                        day — a 1-day signal — and record 1;
--                        high_low_streaks records its config
--                        mean_day_count). INTEGER (whole trading days:
--                        the fractional mean ROUNDS half-up).
--    delayed_signal_days — the bucket's MEAN ANCHOR DELAY: the mean
--                        trading-day offset of the bucket's emitted
--                        trigger anchors within their streaks (0 = the
--                        streak's first qualifying day — when the
--                        signal becomes observable — up to 5), over
--                        the anchors the bucket's merged signals
--                        emitted (one per day 0..min(run_len - 1, 5)
--                        per run). INTEGER, 0 for 1-day / state-family
--                        signals (single delay-0 trigger).
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
--      of forecast_results.threshold).
-- ============================================================================

CREATE TABLE IF NOT EXISTS analysis_forecasts.forecast_identities (
    forecast_id     BIGINT         NOT NULL,  -- surrogate id (forecast_results' sequence)
    sec_type        TEXT           NOT NULL,  -- 'etf' | 'index' | 'stock' (opp_pair: constant 'index')
    code            TEXT           NOT NULL,  -- security ticker; opp_pair: the DROPPING industry_id
    stat_month      DATE           NOT NULL,  -- completed month-end (trailing 5y window end)
    bucket          TEXT           NOT NULL,  -- motivation table name refining this identity
    streak_signal_days INTEGER      NOT NULL DEFAULT 1,  -- mean run length per merged signal (whole days)
    delayed_signal_days INTEGER     NOT NULL DEFAULT 0,  -- mean ANCHOR DELAY of the bucket's emitted triggers (cap 5)

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
CREATE INDEX IF NOT EXISTS idx_forecast_identities_code_bucket
    ON analysis_forecasts.forecast_identities (sec_type, code, bucket, stat_month DESC);

CREATE INDEX IF NOT EXISTS idx_forecast_identities_bucket
    ON analysis_forecasts.forecast_identities (sec_type, bucket, stat_month);

CREATE INDEX IF NOT EXISTS idx_forecast_identities_forecast_id
    ON analysis_forecasts.forecast_identities (forecast_id);

-- ----------------------------------------------------------------------------
--  Comments
-- ----------------------------------------------------------------------------
COMMENT ON TABLE analysis_forecasts.forecast_identities IS 'Identity registry of every forecast bucket: one row per forecast_id holding the shared identity (sec_type, code, stat_month) — the motivation tables store code alone as their partition key, so this is the ONLY table with the full identity — plus the bucket family (the motivation table name — join it on forecast_id for the family-specific axes), the bucket''s mean streak length per merged signal (streak_signal_days), the mean anchor delay (delayed_signal_days) and the recorded lookback_period. 1:1 with each motivation row and its forecast_results period × delay rows; forecast_ids come from the same sequence (forecast_results_forecast_id_seq). code = the forecast subject (security ticker; the DROPPING industry_id for opp_pair_state rows — the forecast-target pair_industry_id lives on that motivation row). PK (code, forecast_id) on HASH (code) partitions — per-security searches prune to one partition; id-only lookups use idx_forecast_identities_forecast_id. Powers search by forecast_id (API / UI / python -m analyze.analysis_forecasts --search-forecast-id). Populated by the writer (same transaction as the motivation + result rows) and by the idempotent backfill in this file; base_rates is NOT in the registry (no forecast_id).';
COMMENT ON COLUMN analysis_forecasts.forecast_identities.forecast_id IS 'Surrogate PK, allocated from the shared sequence analysis_forecasts.forecast_results_forecast_id_seq by the writer — the same id as the bucket''s motivation-table row and its forecast_results period × delay rows.';
COMMENT ON COLUMN analysis_forecasts.forecast_identities.sec_type IS 'Security type: etf (ETF), index (CSI-style index), or stock (individual equity). opp_pair_state rows carry the gate-machinery constant ''index'' (industry_id codes are type=''index'' classification members).';
COMMENT ON COLUMN analysis_forecasts.forecast_identities.code IS 'The forecast SUBJECT: the security ticker for the mov_* / px_vol_state / margin_ratio_state / high_low_streaks families (ETF suffix e.g. "510050.SS"; index bare e.g. "000300"); for opp_pair_state rows the DROPPING industry_id (the trigger side of the pair — the forecast-target pair_industry_id stays on the opp_pair_state motivation row).';
COMMENT ON COLUMN analysis_forecasts.forecast_identities.stat_month IS 'Completed month-end date. The bucket was computed over the trailing 5-year window (stat_month - 5 years, stat_month] of the code''s own trading days.';
COMMENT ON COLUMN analysis_forecasts.forecast_identities.bucket IS 'The motivation TABLE name whose axes refine this identity: mov_rsi / mov_std / mov_pairs / mov_pairs_ema / px_vol_state / margin_ratio_state / high_low_streaks / opp_pair_state. Join analysis_forecasts.<bucket> ON forecast_id for the family-specific bucket config (window / side / pct / k / states / streak band / pair trend_window).';
COMMENT ON COLUMN analysis_forecasts.forecast_identities.streak_signal_days IS 'The bucket''s mean streak length per SIGNAL — the number of days a signal is ACTIVE: the 2026-09 streak-merge groups runs of continuously-qualifying days into ONE forecast signal (emitting incremental anchors per the delay axis), so the event families (mov_rsi / mov_std / mov_pairs / mov_pairs_ema) record the MEAN run length over the bucket''s merged signals; the state families (px_vol_state / margin_ratio_state / opp_pair_state) admit every qualifying day — a 1-day signal — and record 1; high_low_streaks records its config mean_day_count. INTEGER whole trading days (the fractional mean rounds half-up). Column default 1 (pre-streak backfill rows).';
COMMENT ON COLUMN analysis_forecasts.forecast_identities.delayed_signal_days IS 'The bucket''s MEAN ANCHOR DELAY — the mean trading-day offset of the bucket''s emitted trigger anchors within their streaks (0 = the streak''s first qualifying day, when the signal becomes observable; a run of length d emits anchors at offsets 0..min(d - 1, 5), per-run mean (LEAST(d, 6) - 1) / 2.0), CAPPED at 5. 0 for 1-day signals (the state families / cross days — a single delay-0 trigger). INTEGER; column default 0 (pre-migration backfill rows; real means arrive with the refresh / --force rebuild).';
COMMENT ON COLUMN analysis_forecasts.forecast_identities.lookback_period IS 'Recorded build parameter (NOT a key member): the trailing calendar window the bucket was computed over — ''5y'' = (stat_month - 5 years, stat_month]. Mirrors the same column on the motivation and result tables.';

-- ----------------------------------------------------------------------------
--  Data-quality gates (CHECK constraints) — garbage-in protection for the
--  forecast result store, via the shared helpers of
--  database/sql/00_partition_utils.sql: ensure_check_constraint adds each
--  gate IDEMPOTENTLY as NOT VALID (a brief metadata-only lock — the
--  writer's COPY is never blocked), validate_pending_checks VALIDATEs the
--  schema's still-unvalidated chk_% constraints (the scan runs once;
--  re-applies are no-ops). CHECKs pass on NULL, so the nullable stat
--  columns are gated without touching their NULL semantics. A validation
--  failure surfaces REAL data bugs — fix the data, never the gate.
-- ----------------------------------------------------------------------------
-- The period vocabulary narrowed with the 2026-09-20 horizon-60d
-- removal — drop the retired gate first (ensure_check_constraint
-- skips a same-named constraint regardless of its definition), then
-- re-add over the surviving periods. The '60d' rows are already gone
-- (purged by the migration above), so the validation scan passes.
ALTER TABLE analysis_forecasts.forecast_results
    DROP CONSTRAINT IF EXISTS chk_forecast_results_period;
SELECT public.ensure_check_constraint(
    'analysis_forecasts.forecast_results',
    'chk_forecast_results_period',
    $chk$period IN ('next', '5d', '20d', 'mixed')$chk$);
SELECT public.ensure_check_constraint(
    'analysis_forecasts.forecast_results',
    'chk_forecast_results_reverse_prob',
    $chk$reverse_prob BETWEEN 0 AND 1$chk$);
SELECT public.ensure_check_constraint(
    'analysis_forecasts.forecast_results',
    'chk_forecast_results_occurrence_count',
    $chk$occurrence_count >= 0$chk$);
SELECT public.ensure_check_constraint(
    'analysis_forecasts.forecast_results',
    'chk_forecast_results_threshold',
    $chk$threshold > 0$chk$);

-- forecast_identities: the family registry vocabulary, the sec_type
-- domain, positive mean streak lengths, trigger delays in [0, 5].
SELECT public.ensure_check_constraint(
    'analysis_forecasts.forecast_identities',
    'chk_forecast_identities_bucket',
    $chk$bucket IN ('mov_rsi', 'mov_std', 'mov_pairs', 'mov_pairs_ema',
                    'px_vol_state', 'margin_ratio_state', 'opp_pair_state',
                    'high_low_streaks', 'pe_state', 'dividend_state')$chk$);
SELECT public.ensure_check_constraint(
    'analysis_forecasts.forecast_identities',
    'chk_forecast_identities_sec_type',
    $chk$sec_type IN ('etf', 'index', 'stock')$chk$);
SELECT public.ensure_check_constraint(
    'analysis_forecasts.forecast_identities',
    'chk_forecast_identities_streak_signal_days',
    $chk$streak_signal_days > 0$chk$);
-- The delay gate narrowed to the [0, 5] cap — drop the retired >= 0
-- form first (ensure_check_constraint skips a same-named constraint
-- regardless of its definition); the capped backfill above has already
-- brought every row inside the range, so the validation scan passes.
ALTER TABLE analysis_forecasts.forecast_identities
    DROP CONSTRAINT IF EXISTS chk_forecast_identities_delayed_signal_days;
SELECT public.ensure_check_constraint(
    'analysis_forecasts.forecast_identities',
    'chk_forecast_identities_delayed_signal_days',
    $chk$delayed_signal_days BETWEEN 0 AND 5$chk$);

SELECT public.validate_pending_checks('analysis_forecasts');

-- ----------------------------------------------------------------------------
--  Orphan reconciliation — the forecast_id ↔ forecast_identities invariant.
--
--  Every forecast_results row MUST belong to a registered bucket: the
--  writer allocates the forecast_id and inserts the identity + result rows
--  in ONE transaction, so a committed result without an identity row is
--  orphaned debris (historical partial deletions / retired-family purges
--  that predate the registry-resolved deletes). There is deliberately NO
--  FOREIGN KEY enforcing this: forecast_identities is HASH(code)-
--  partitioned, and a partitioned table cannot carry a unique index on
--  forecast_id alone (a unique constraint must include the partition key),
--  while a row-level EXISTS trigger would tax the writer's 100K-row COPY
--  chunks. The substitute is this RECONCILIATION PURGE, idempotent on every
--  apply: results whose identity is gone are unreferenceable debris (no
--  bucket, no family, no API/UI join) and are deleted. Re-running against a
--  clean store is a no-op anti-join.
-- ----------------------------------------------------------------------------
DELETE FROM analysis_forecasts.forecast_results f
WHERE NOT EXISTS (
    SELECT 1 FROM analysis_forecasts.forecast_identities i
    WHERE i.forecast_id = f.forecast_id
);
