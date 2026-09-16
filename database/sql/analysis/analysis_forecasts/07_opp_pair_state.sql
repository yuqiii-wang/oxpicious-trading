-- ============================================================================
--  Table: analysis_forecasts.opp_pair_state
--
--  Sixth MOTIVATION (bucket-defining) table of the forecast analysis:
--  INDUSTRY OPPOSITE-PAIR trend buckets over the pair set of
--  analysis_composites.industry_corr_benchmark_offsets (pool 'all',
--  benchmark 000300). By PAIR: when ONE industry's benchmark-offset
--  trend is dropping, the forecast RESULT is the future trend of the
--  OTHER side industry.
--
--  MATH (all legs on the OFFSET space the composites analysis defines)
--    MA_X[t]  = trailing-W-row rolling mean of the industry composite
--               mean_close (stats.industry_basic_stats, pool 'all').
--    MA_M[t]  = trailing-W-row rolling mean of the benchmark's
--               (stats.index_basic_stats.close) close.
--    The W-day offset trend change of industry X ending at t, with the
--    benchmark rebased at the lookback start (k = MA_X[t-W]/MA_M[t-W],
--    the composites' window math — the adjusted trend adj = MA_X - k·MA_M
--    is identically 0 at the rebasing point), normalized by the
--    industry's own MA level, reduces to the RELATIVE MA RETURN
--      rel_X(t) = MA_X[t]/MA_X[t-W] - MA_M[t]/MA_M[t-W].
--    TRIGGER ("industry A is dropping"): rel_A(t) < 0 — A's W-day
--    MA-trend return is below the benchmark's (an industry whose trend
--    grows while the benchmark grows MORE is DROPPING after the offset).
--    FORWARD TARGET (the forecast result): the OTHER side industry B's
--    normalized offset change over [t, t+n]
--      fwd_B(t,n) = MA_B[t+n]/MA_B[t] - MA_M[t+n]/MA_M[t].
--
--  BUCKETS
--    One row per (sec_type, industry_id = the dropping industry A,
--    pair_industry_id = the forecast target B, stat_month, trend_window
--    W). Every unordered pair of the offsets table is materialized in
--    BOTH directions (A drops → B forecast; B drops → A forecast).
--    trend_window W ∈ {20, 60} (the composites' short/medium trend
--    scale; 255 is a regime filter, too slow for day-level buckets).
--    STATE buckets: every qualifying day joins — no cooldown, no
--    is_market_hyped split (industries have no hype source). The side
--    is the constant 'bottom' so the shared gate machinery reads the
--    table unchanged.
--
--  RESULT DATA
--    analysis_forecasts.forecast_results via forecast_id (1:N — one
--    forecast_id → 5 period rows next/5d/20d/60d/mixed): B's forward offset
--    change stats (ave/std/max/min, occurrence_count,
--    max_low_change_ratio) and reverse_prob at B's ADAPTIVE
--    threshold (k_n·σ of B's window forward offset changes).
--    side='bottom' → reverse_prob = P(B's change > +threshold)
--    = the pair forecast's CONFIRMATION probability (B rises when A
--    drops) — NOT a reversal probability. The signals layer reads the
--    cross-period MAX(reverse_prob) as each signal row's confidence.
--
--  The config JSONB records the bucket's mean trigger trend (mean_rel)
--  and the pair's latest offsets-table context (pair_score /
--  pair_corr / score_date — a provenance snapshot, not a trigger
--  input: the triggers/targets use only window-internal data, so no
--  look-ahead).
--
--  CODE-CLUSTERED, forecast_id-keyed (2026-09): PK (industry_id, forecast_id)
--  on HASH (industry_id) partitions — the code-clustered read/write axis (a
--  per-security read prunes to ONE partition and walks the code-leading
--  PK; forecast_id-only joins/searches use the secondary
--  idx_opp_pair_state_forecast_id). industry_id is the ONLY identity column stored
--  here (the partition key); sec_type and stat_month live ONLY in
--  analysis_forecasts.forecast_identities — the search table
--  (see 02_mov_rsi_mov_std.sql). pair_industry_id (the forecast
--  target) is a bucket metric, NOT identity — it stays on this
--  table.
--
--  sec_type semantics (registered on the identities row): the constant
--  'index' (industry_id codes are type='index' classification members)
--  keeps the shared month-gating / gate machinery working — the
--  universe is the offsets-table pair set, NOT stats.index_identity
--  codes.
--
--  SOURCE
--    stats.industry_basic_stats.mean_close               (pool 'all')
--    stats.index_basic_stats.close                        (benchmark)
--    analysis_composites.industry_corr_benchmark_offsets  (pair set)
--
--  POPULATION
--    python -m analyze.analysis_forecasts
--      incremental: missing stat_months + refresh of the last
--      REFRESH_MONTHS; --force: delete + full recompute.
-- ============================================================================

CREATE TABLE IF NOT EXISTS analysis_forecasts.opp_pair_state (
    industry_id     TEXT    NOT NULL,  -- hash partition key + PK lead (the DROPPING industry, registered as identities.code); sec_type / stat_month live in forecast_identities
    forecast_id       BIGINT  NOT NULL,  -- PK + hash key; 1:N link → forecast_results (5 period rows); identity (sec_type, code = dropping industry_id, stat_month) + bucket family live in forecast_identities
    pair_industry_id  TEXT    NOT NULL,  -- the OTHER side — whose future trend the forecast_results rows describe (bucket metric, NOT identity)
    trend_window      INTEGER NOT NULL,  -- W of the MA curves (20 | 60)

    -- Constant reversal side: 'bottom' — reverse_prob = P(the other side
    -- industry's forward offset change > +threshold), the pair
    -- forecast's CONFIRMATION probability.
    side              TEXT    NOT NULL,

    -- Recorded build parameters (NOT PK — provenance; a rebuild with
    -- different values requires --force).
    benchmark_code    TEXT    NOT NULL DEFAULT '000300',
    pool_size         TEXT    NOT NULL DEFAULT 'all',
    lookback_period   TEXT    NOT NULL DEFAULT '5y',  -- trailing window the bucket was computed over ('5y' = stat_month - 5y .. stat_month)

    CONSTRAINT pk_opp_pair_state PRIMARY KEY (industry_id, forecast_id)
) PARTITION BY HASH (industry_id);

SELECT public.create_hash_partitions('analysis_forecasts', 'opp_pair_state', 16);

-- ----------------------------------------------------------------------------
--  Migration (2026-09): forecast_id-keyed rebuild — the composite
--  (sec_type, industry_id, pair_industry_id, stat_month, trend_window)
--  PK is replaced by the surrogate forecast_id (PK + hash-partition
--  key); the shared identity lives ONLY in forecast_identities (code =
--  the dropping industry_id; pair_industry_id stays on this table).
--  Legacy-shape tables (they still carry the industry_id column) are
--  rebuilt by swap — rows are carried over via forecast_id and the old
--  secondary forecast_id index dissolves into the new PK. See
--  02_mov_rsi_mov_std.sql for the full rationale.
-- ----------------------------------------------------------------------------
DO $$
DECLARE
    r          int;
    v_partkey  text;
    v_pkdef    text;
    v_has_code bool;
BEGIN
    SELECT pg_get_partkeydef(c.oid),
           COALESCE((SELECT pg_get_constraintdef(p.oid)
                     FROM pg_constraint p
                     WHERE p.conrelid = c.oid AND p.contype = 'p'), '')
    INTO v_partkey, v_pkdef
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'analysis_forecasts' AND c.relname = 'opp_pair_state';
    SELECT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_schema = 'analysis_forecasts'
                     AND table_name   = 'opp_pair_state'
                     AND column_name  = 'industry_id')
    INTO v_has_code;
    IF v_partkey IS NULL
       OR (v_partkey = 'HASH (industry_id)'
           AND v_pkdef = 'PRIMARY KEY (industry_id, forecast_id)') THEN
        -- fresh install (created above in the target shape) or already
        -- migrated
        RETURN;
    END IF;
    ALTER TABLE analysis_forecasts.opp_pair_state RENAME TO opp_pair_state_pk_rebuild;
    ALTER TABLE analysis_forecasts.opp_pair_state_pk_rebuild
        DROP CONSTRAINT IF EXISTS pk_opp_pair_state;
    CREATE TABLE analysis_forecasts.opp_pair_state_new (
            industry_id     TEXT    NOT NULL,  -- hash partition key + PK lead (the DROPPING industry, registered as identities.code); sec_type / stat_month live in forecast_identities
            forecast_id       BIGINT  NOT NULL,  -- PK + hash key; 1:N link → forecast_results (5 period rows); identity (sec_type, code = dropping industry_id, stat_month) + bucket family live in forecast_identities
            pair_industry_id  TEXT         NOT NULL,
            trend_window      INTEGER      NOT NULL,
            side              TEXT         NOT NULL,
            benchmark_code    TEXT         NOT NULL DEFAULT '000300',
            pool_size         TEXT         NOT NULL DEFAULT 'all',
            lookback_period   TEXT         NOT NULL DEFAULT '5y',
        CONSTRAINT pk_opp_pair_state PRIMARY KEY (industry_id, forecast_id)
    ) PARTITION BY HASH (industry_id);
    PERFORM public.create_hash_partitions('analysis_forecasts',
                                          'opp_pair_state_new', 16);
    ALTER TABLE analysis_forecasts.opp_pair_state_pk_rebuild
        ADD COLUMN IF NOT EXISTS lookback_period TEXT NOT NULL DEFAULT '5y';
    IF v_has_code THEN
        INSERT INTO analysis_forecasts.opp_pair_state_new
               (industry_id, forecast_id, pair_industry_id, trend_window, side, benchmark_code, pool_size, lookback_period)
        SELECT  industry_id, forecast_id, pair_industry_id, trend_window, side, benchmark_code, pool_size, lookback_period
        FROM    analysis_forecasts.opp_pair_state_pk_rebuild;
    ELSE
        -- intermediate forecast_id-keyed shape (no industry_id column): code
        -- comes from the identities registry (1 row per forecast_id)
        INSERT INTO analysis_forecasts.opp_pair_state_new
               (industry_id, forecast_id, pair_industry_id, trend_window, side, benchmark_code, pool_size, lookback_period)
        SELECT  i.code, m.forecast_id, m.pair_industry_id, m.trend_window, m.side, m.benchmark_code, m.pool_size, m.lookback_period
        FROM    analysis_forecasts.opp_pair_state_pk_rebuild m
        JOIN    analysis_forecasts.forecast_identities i
          ON    i.forecast_id = m.forecast_id;
    END IF;
    DROP TABLE analysis_forecasts.opp_pair_state_pk_rebuild;
    ALTER TABLE analysis_forecasts.opp_pair_state_new RENAME TO opp_pair_state;
    FOR r IN 0..15 LOOP
        EXECUTE format(
            'ALTER TABLE analysis_forecasts.opp_pair_state_new_p%s '
            'RENAME TO opp_pair_state_p%s',
            lpad(r::text, 2, '0'), lpad(r::text, 2, '0'));
    END LOOP;
END $$;

CREATE INDEX IF NOT EXISTS idx_opp_pair_state_forecast_id
    ON analysis_forecasts.opp_pair_state (forecast_id);

-- ----------------------------------------------------------------------------
--  Comments
-- ----------------------------------------------------------------------------
COMMENT ON TABLE analysis_forecasts.opp_pair_state IS 'Industry opposite-pair trend buckets (motivation): one row per forecast_id — the trailing-5y-window days where industry A''s W-day benchmark-offset MA trend is dropping (rel_A(t) = MA_A[t]/MA_A[t-W] - MA_M[t]/MA_M[t-W] < 0, the composites'' offset math normalized to a relative MA return), with the OTHER side industry B (pair_industry_id)''s forward offset change (MA_B[t+n]/MA_B[t] - MA_M[t+n]/MA_M[t]) as the forecast result in analysis_forecasts.forecast_results via forecast_id. Every unordered pair of analysis_composites.industry_corr_benchmark_offsets (pool ''all'', benchmark 000300), both directions; state buckets (no cooldown, no hype split); side constant ''bottom'' so reverse_prob = P(B rises beyond B''s FIXED 1% threshold — the period-end n-day offset change vs ±1%) — the pair forecast''s CONFIRMATION probability. Keyed by the surrogate forecast_id (hash partition key); the shared identity (sec_type=''index'', code = the DROPPING industry_id, stat_month) + bucket family live in analysis_forecasts.forecast_identities. Sources: stats.industry_basic_stats.mean_close + stats.index_basic_stats.close + analysis_composites.industry_corr_benchmark_offsets. Populated by python -m analyze.analysis_forecasts.';
COMMENT ON COLUMN analysis_forecasts.opp_pair_state.forecast_id IS 'Surrogate PK + hash-partition key (1:N link to the bucket''s 5 period rows in analysis_forecasts.forecast_results, allocated by the writer, shared across all 5 periods). The bucket''s identity (sec_type=''index'', code = the DROPPING industry_id, stat_month) + bucket family are registered in analysis_forecasts.forecast_identities under this id; the forecast-target pair_industry_id stays on this row.';
COMMENT ON COLUMN analysis_forecasts.opp_pair_state.pair_industry_id IS 'The OTHER side of the pair — the forecast TARGET: the linked forecast_results rows describe THIS industry''s forward offset trend changes. The confirmation-gate calibration (analysis_signals.gate) groups by this column, and signal rows are emitted on it. (The DROPPING industry_id is identity — registered as the identities row''s code.)';
COMMENT ON COLUMN analysis_forecasts.opp_pair_state.trend_window IS 'Trend window W (trading-day rows) of the MA curves the relative MA returns are computed on: 20 | 60.';
COMMENT ON COLUMN analysis_forecasts.opp_pair_state.side IS 'Constant ''bottom'': reverse_prob = P(the other side industry''s forward offset change > +threshold) — the pair forecast''s CONFIRMATION probability (B rises when A drops), at B''s FIXED 1% threshold (the period-end n-day offset change vs ±1%). Mirrors the mov_* side semantics so analysis_signals.gate consumes the table unchanged (bottom → action buy on the target).';
COMMENT ON COLUMN analysis_forecasts.opp_pair_state.benchmark_code IS 'Recorded build parameter: the offset benchmark index code (default 000300 = CSI300) whose MA curve is subtracted (level-rebased) in every relative MA return.';
COMMENT ON COLUMN analysis_forecasts.opp_pair_state.pool_size IS 'Recorded build parameter: the stats.industry_basic_stats pool slice of the composite closes AND the offsets-table slice the pair set is read from (default ''all'').';
COMMENT ON COLUMN analysis_forecasts.opp_pair_state.lookback_period IS 'Recorded build parameter (NOT a PK member): the trailing calendar window the bucket was computed over — ''5y'' = (stat_month - 5 years, stat_month] of the union industry calendar. Default ''5y''; a rebuild with a different lookback requires --force.';
