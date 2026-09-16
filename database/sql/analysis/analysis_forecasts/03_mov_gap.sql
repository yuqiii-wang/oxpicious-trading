-- ============================================================================
--  Table: analysis_forecasts.mov_gap
--
--  Third MOTIVATION (bucket-defining) table of the forecast analysis:
--  short-term price-gap (N-day return) extreme-percentile buckets —
--  the exact mov_rsi machinery applied to the gap_{W}days columns
--  (W-day price return (price[t] - price[t-W]) / price[t-W], stored in
--  analysis.mov_ave_rsi alongside the RSI columns).
--
--  A day joins the bucket when gap_{W}days sits in the top pct%
--  (side=top — a sharp W-day rally, overbought) or bottom pct%
--  (side=bottom — a sharp W-day selloff, oversold) of the trailing
--  5-year window's non-NULL gap_{W}days values (linear-interpolated
--  percentile per code), streak-merged exactly like mov_rsi / mov_std
--  (2026-09: consecutive bucket days are ONE forecast signal anchored
--  at the run's MID day; the legacy fixed-5-day cooldown was removed).
--  gap_window ∈ {2, 3} (the analysis.mov_ave_rsi
--  gap_2days / gap_3days columns); pct ∈ {1, 5, 10, 25}.
--
--  CODE-CLUSTERED, forecast_id-keyed (2026-09): PK (code, forecast_id)
--  on HASH (code) partitions — the code-clustered read/write axis (a
--  per-security read prunes to ONE partition and walks the code-leading
--  PK; forecast_id-only joins/searches use the secondary
--  idx_mov_gap_forecast_id). code is the ONLY identity column stored
--  here (the partition key); sec_type and stat_month live ONLY in
--  analysis_forecasts.forecast_identities — the search table
--  (see 02_mov_rsi_mov_std.sql).
--
--  Full-window gate + hype split + forecast_id link: identical to
--  mov_rsi (see 02_mov_rsi_mov_std.sql). Results (forward changes /
--  reversal probabilities) live in analysis_forecasts.forecast_results
--  via forecast_id (1:N — one forecast_id → 5 period rows).
-- ============================================================================

CREATE TABLE IF NOT EXISTS analysis_forecasts.mov_gap (
    code            TEXT         NOT NULL,  -- hash partition key + PK lead; sec_type / stat_month live in forecast_identities
    forecast_id     BIGINT       NOT NULL,  -- 1:N link to the bucket's 5 forecast_results period rows; id-only joins/searches use idx_mov_gap_forecast_id
    gap_window      INTEGER      NOT NULL,  -- gap window in trading days: 2/3 (gap_{W}days N-day return)
    side            TEXT         NOT NULL,  -- 'top' (sharp rally) | 'bottom' (sharp selloff)
    pct             INTEGER      NOT NULL,  -- percentile width: 1 / 5 / 10 / 25

    -- recorded build parameter (NOT PK): trailing window the bucket was
    -- computed over — '5y' = (stat_month - 5y, stat_month]
    lookback_period TEXT         NOT NULL DEFAULT '5y',

    -- motivation cols
    is_market_hyped BOOLEAN      NOT NULL,  -- ANY bucket date inside a mov_ave_market_hypes episode (any check-in period)

    CONSTRAINT pk_mov_gap PRIMARY KEY (code, forecast_id)
) PARTITION BY HASH (code);

SELECT public.create_hash_partitions('analysis_forecasts', 'mov_gap', 16);

-- ----------------------------------------------------------------------------
--  Migration (2026-09): forecast_id-keyed rebuild — the composite
--  (code, sec_type, stat_month, ...) PK is replaced by the surrogate
--  forecast_id (PK + hash-partition key); the shared identity lives
--  ONLY in forecast_identities. Legacy-shape tables (they still carry
--  the code column) are rebuilt by swap — rows are carried over via
--  forecast_id and the old secondary forecast_id index dissolves into
--  the new PK. See 02_mov_rsi_mov_std.sql for the full rationale.
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
    WHERE n.nspname = 'analysis_forecasts' AND c.relname = 'mov_gap';
    SELECT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_schema = 'analysis_forecasts'
                     AND table_name   = 'mov_gap'
                     AND column_name  = 'code')
    INTO v_has_code;
    IF v_partkey IS NULL
       OR (v_partkey = 'HASH (code)'
           AND v_pkdef = 'PRIMARY KEY (code, forecast_id)') THEN
        -- fresh install (created above in the target shape) or already
        -- migrated
        RETURN;
    END IF;
    ALTER TABLE analysis_forecasts.mov_gap RENAME TO mov_gap_pk_rebuild;
    ALTER TABLE analysis_forecasts.mov_gap_pk_rebuild
        DROP CONSTRAINT IF EXISTS pk_mov_gap;
    CREATE TABLE analysis_forecasts.mov_gap_new (
            code            TEXT         NOT NULL,  -- hash partition key + PK lead; sec_type / stat_month live in forecast_identities
            forecast_id     BIGINT       NOT NULL,  -- 1:N link to the bucket's 5 forecast_results period rows; id-only joins/searches use idx_mov_gap_forecast_id
            gap_window      INTEGER      NOT NULL,
            side            TEXT         NOT NULL,
            pct             INTEGER      NOT NULL,
            lookback_period TEXT         NOT NULL DEFAULT '5y',
            is_market_hyped BOOLEAN      NOT NULL,
        CONSTRAINT pk_mov_gap PRIMARY KEY (code, forecast_id)
    ) PARTITION BY HASH (code);
    PERFORM public.create_hash_partitions('analysis_forecasts',
                                          'mov_gap_new', 16);
    ALTER TABLE analysis_forecasts.mov_gap_pk_rebuild
        ADD COLUMN IF NOT EXISTS lookback_period TEXT NOT NULL DEFAULT '5y';
    IF v_has_code THEN
        INSERT INTO analysis_forecasts.mov_gap_new
               (code, forecast_id, gap_window, side, pct, lookback_period, is_market_hyped)
        SELECT  code, forecast_id, gap_window, side, pct, lookback_period, is_market_hyped
        FROM    analysis_forecasts.mov_gap_pk_rebuild;
    ELSE
        -- intermediate forecast_id-keyed shape (no code column): code
        -- comes from the identities registry (1 row per forecast_id)
        INSERT INTO analysis_forecasts.mov_gap_new
               (code, forecast_id, gap_window, side, pct, lookback_period, is_market_hyped)
        SELECT  i.code, m.forecast_id, m.gap_window, m.side, m.pct, m.lookback_period, m.is_market_hyped
        FROM    analysis_forecasts.mov_gap_pk_rebuild m
        JOIN    analysis_forecasts.forecast_identities i
          ON    i.forecast_id = m.forecast_id;
    END IF;
    DROP TABLE analysis_forecasts.mov_gap_pk_rebuild;
    ALTER TABLE analysis_forecasts.mov_gap_new RENAME TO mov_gap;
    FOR r IN 0..15 LOOP
        EXECUTE format(
            'ALTER TABLE analysis_forecasts.mov_gap_new_p%s '
            'RENAME TO mov_gap_p%s',
            lpad(r::text, 2, '0'), lpad(r::text, 2, '0'));
    END LOOP;
END $$;

CREATE INDEX IF NOT EXISTS idx_mov_gap_forecast_id
    ON analysis_forecasts.mov_gap (forecast_id);

-- ----------------------------------------------------------------------------
--  Comments
-- ----------------------------------------------------------------------------
COMMENT ON TABLE analysis_forecasts.mov_gap IS 'Short-term price-gap (N-day return) extreme-day bucket definitions (motivation): one row per forecast_id — the days of one security-month whose gap_{W}days = (price[t]-price[t-W])/price[t-W] is in the top pct% (side=top, sharp W-day rally) or bottom pct% (side=bottom, sharp W-day selloff) of the trailing 5-year window ending at the bucket''s stat_month, streak-merged (2026-09: consecutive bucket days are ONE forecast signal anchored at the run''s MID day — the mean run length per signal lives on forecast_identities.streak_signal_days; the legacy fixed-5-day cooldown was removed), split by whether any bucket date is a market-hyped date. gap_window ∈ {2, 3} mirrors analysis.mov_ave_rsi.gap_2days / gap_3days. Keyed by the surrogate forecast_id (hash partition key); the shared identity (sec_type, code, stat_month) + bucket family live in analysis_forecasts.forecast_identities. Results (forward changes / reversal probabilities) live in analysis_forecasts.forecast_results via forecast_id. Source: analysis.mov_ave_rsi (gap columns).';
COMMENT ON COLUMN analysis_forecasts.mov_gap.forecast_id IS 'Surrogate PK + hash-partition key (1:N link to the bucket''s 5 period rows in analysis_forecasts.forecast_results, allocated by the writer, shared across all 5 periods). The bucket''s identity (sec_type, code, stat_month) + bucket family are registered in analysis_forecasts.forecast_identities under this id.';
COMMENT ON COLUMN analysis_forecasts.mov_gap.gap_window IS 'N-day price-return window (trading days) whose extreme days are bucketed: 2/3 — mirrors analysis.mov_ave_rsi.gap_2days / gap_3days.';
COMMENT ON COLUMN analysis_forecasts.mov_gap.side IS 'Bucket side: top = gap_{W}days in the top pct% of the window (sharp rally; reversals are changes below the bucket''s FIXED 1% threshold (0.01 — the period-end n-day close vs ±1%); bottom = gap_{W}days in the bottom pct% (sharp selloff; reversals are changes above it).';
COMMENT ON COLUMN analysis_forecasts.mov_gap.pct IS 'Percentile width of the bucket: 1, 5, 10 or 25 (percent). The threshold is the window''s (linear-interpolated) percentile of gap_{W}days over non-NULL values.';
COMMENT ON COLUMN analysis_forecasts.mov_gap.is_market_hyped IS 'TRUE when ANY of the bucket''s dates falls inside one of the code''s stats.mov_ave_market_hypes episodes (any min_checkin_period).';
COMMENT ON COLUMN analysis_forecasts.mov_gap.lookback_period IS 'Recorded build parameter (NOT a PK member): the trailing calendar window the bucket was computed over — ''5y'' = (stat_month - 5 years, stat_month]. Default ''5y''; a rebuild with a different lookback requires --force.';
