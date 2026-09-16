-- ============================================================================
--  Table: analysis_forecasts.mov_pairs
--
--  Seventh MOTIVATION (bucket-defining) table of the forecast analysis:
--  MA-pair CROSS (golden / death cross) event buckets built on the
--  EXISTING relative-MA-spread columns of
--  analysis.mov_ave_spreads_detail — ma5_vs_ma{W} =
--  (ma5 - ma_{W}) / ma_{W}, the parent mov_ave_spread analysis's own
--  spread definition. NO new MA computation: a cross day is read
--  directly off the stored spread's sign flip.
--
--  (EMA sibling: 09_mov_pairs_ema.sql reads the same cross machinery
--  off analysis.mov_ave_spreads_detail_ema.ema6_vs_ema{W}.)
--
--  A day joins a bucket when the pair's spread changes sign that day:
--
--    side=top    — CROSS UP   (golden cross): ma5_vs_ma{W}[t] >  0 and
--                  ma5_vs_ma{W}[t-1] <= 0 (the fast MA5 rises through
--                  the slow MA_{W} — the pair turns bullish)
--    side=bottom — CROSS DOWN (death  cross): ma5_vs_ma{W}[t] <  0 and
--                  ma5_vs_ma{W}[t-1] >= 0 (the fast MA5 falls through
--                  the slow MA_{W} — the pair turns bearish)
--
--  (the [t-1] leg is the code's PREVIOUS trading-grid row; NULL
--  spreads — either MA still warming up — never trigger). Triggers are
--  ONE-DAY signals: a cross day's predecessor sits on the other side
--  of zero, so consecutive cross days are mutually exclusive — every
--  cross day is its own forecast signal (streak_signal_days = 1; the
--  legacy fixed-5-day cooldown was removed 2026-09, and no
--  streak-merge pass is needed). pair_window ∈ {60, 120, 255}
--  (the slow leg; ma5_vs_ma20 exists in the source but is not built).
--
--  CODE-CLUSTERED, forecast_id-keyed (2026-09): PK (code, forecast_id)
--  on HASH (code) partitions — the code-clustered read/write axis (a
--  per-security read prunes to ONE partition and walks the code-leading
--  PK; forecast_id-only joins/searches use the secondary
--  idx_mov_pairs_forecast_id). code is the ONLY identity column stored
--  here (the partition key); sec_type and stat_month live ONLY in
--  analysis_forecasts.forecast_identities — the search table
--  (see 02_mov_rsi_mov_std.sql).
--
--  Full-window gate + hype split + forecast_id link: identical to
--  mov_rsi / mov_gap (see 02_mov_rsi_mov_std.sql). Results (forward
--  changes / reversal probabilities) live in
--  analysis_forecasts.forecast_results via forecast_id (1:N — one
--  forecast_id → 5 period rows: next/5d/20d/60d/mixed).
-- ============================================================================

CREATE TABLE IF NOT EXISTS analysis_forecasts.mov_pairs (
    code            TEXT         NOT NULL,  -- hash partition key + PK lead; sec_type / stat_month live in forecast_identities
    forecast_id     BIGINT       NOT NULL,  -- 1:N link to the bucket's 5 forecast_results period rows; id-only joins/searches use idx_mov_pairs_forecast_id
    pair_window     INTEGER      NOT NULL,  -- slow MA leg of the pair (trading days): 60/120/255 (fast leg fixed ma5)
    side            TEXT         NOT NULL,  -- 'top' (cross up / golden cross) | 'bottom' (cross down / death cross)

    -- recorded build parameter (NOT PK): trailing window the bucket was
    -- computed over — '5y' = (stat_month - 5y, stat_month]
    lookback_period TEXT         NOT NULL DEFAULT '5y',

    -- motivation cols
    is_market_hyped BOOLEAN      NOT NULL,  -- ANY bucket date inside a mov_ave_market_hypes episode (any check-in period)

    CONSTRAINT pk_mov_pairs PRIMARY KEY (code, forecast_id)
) PARTITION BY HASH (code);

SELECT public.create_hash_partitions('analysis_forecasts', 'mov_pairs', 16);

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
    WHERE n.nspname = 'analysis_forecasts' AND c.relname = 'mov_pairs';
    SELECT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_schema = 'analysis_forecasts'
                     AND table_name   = 'mov_pairs'
                     AND column_name  = 'code')
    INTO v_has_code;
    IF v_partkey IS NULL
       OR (v_partkey = 'HASH (code)'
           AND v_pkdef = 'PRIMARY KEY (code, forecast_id)') THEN
        -- fresh install (created above in the target shape) or already
        -- migrated
        RETURN;
    END IF;
    ALTER TABLE analysis_forecasts.mov_pairs RENAME TO mov_pairs_pk_rebuild;
    ALTER TABLE analysis_forecasts.mov_pairs_pk_rebuild
        DROP CONSTRAINT IF EXISTS pk_mov_pairs;
    CREATE TABLE analysis_forecasts.mov_pairs_new (
            code            TEXT         NOT NULL,  -- hash partition key + PK lead; sec_type / stat_month live in forecast_identities
            forecast_id     BIGINT       NOT NULL,  -- 1:N link to the bucket's 5 forecast_results period rows; id-only joins/searches use idx_mov_pairs_forecast_id
            pair_window     INTEGER      NOT NULL,
            side            TEXT         NOT NULL,
            lookback_period TEXT         NOT NULL DEFAULT '5y',
            is_market_hyped BOOLEAN      NOT NULL,
        CONSTRAINT pk_mov_pairs PRIMARY KEY (code, forecast_id)
    ) PARTITION BY HASH (code);
    PERFORM public.create_hash_partitions('analysis_forecasts',
                                          'mov_pairs_new', 16);
    ALTER TABLE analysis_forecasts.mov_pairs_pk_rebuild
        ADD COLUMN IF NOT EXISTS lookback_period TEXT NOT NULL DEFAULT '5y';
    IF v_has_code THEN
        INSERT INTO analysis_forecasts.mov_pairs_new
               (code, forecast_id, pair_window, side, lookback_period, is_market_hyped)
        SELECT  code, forecast_id, pair_window, side, lookback_period, is_market_hyped
        FROM    analysis_forecasts.mov_pairs_pk_rebuild;
    ELSE
        -- intermediate forecast_id-keyed shape (no code column): code
        -- comes from the identities registry (1 row per forecast_id)
        INSERT INTO analysis_forecasts.mov_pairs_new
               (code, forecast_id, pair_window, side, lookback_period, is_market_hyped)
        SELECT  i.code, m.forecast_id, m.pair_window, m.side, m.lookback_period, m.is_market_hyped
        FROM    analysis_forecasts.mov_pairs_pk_rebuild m
        JOIN    analysis_forecasts.forecast_identities i
          ON    i.forecast_id = m.forecast_id;
    END IF;
    DROP TABLE analysis_forecasts.mov_pairs_pk_rebuild;
    ALTER TABLE analysis_forecasts.mov_pairs_new RENAME TO mov_pairs;
    FOR r IN 0..15 LOOP
        EXECUTE format(
            'ALTER TABLE analysis_forecasts.mov_pairs_new_p%s '
            'RENAME TO mov_pairs_p%s',
            lpad(r::text, 2, '0'), lpad(r::text, 2, '0'));
    END LOOP;
END $$;

CREATE INDEX IF NOT EXISTS idx_mov_pairs_forecast_id
    ON analysis_forecasts.mov_pairs (forecast_id);

-- ----------------------------------------------------------------------------
--  Comments
-- ----------------------------------------------------------------------------
COMMENT ON TABLE analysis_forecasts.mov_pairs IS 'MA-pair cross (golden/death cross) bucket definitions (motivation): one row per forecast_id — the days of one security-month within the trailing 5-year window ending at the bucket''s stat_month where the stored relative-MA spread analysis.mov_ave_spreads_detail.ma5_vs_ma{pair_window} = (ma5 - ma_{W}) / ma_{W} changes sign: side=top a CROSS UP / golden cross (spread turns > 0 from <= 0, ma5 rises through the slow MA), side=bottom a CROSS DOWN / death cross (spread turns < 0 from >= 0), one-day signals (2026-09: the legacy fixed-5-day cooldown was removed; a cross day''s predecessor sits on the other side of zero so consecutive cross days are mutually exclusive — every cross day is its own forecast signal with streak_signal_days = 1). Keyed by the surrogate forecast_id (hash partition key); the shared identity (sec_type, code, stat_month) + bucket family live in analysis_forecasts.forecast_identities. Results (forward changes / reversal probabilities) live in analysis_forecasts.forecast_results via forecast_id. NO new MA computation — the buckets read the parent mov_ave_spread analysis''s existing spread columns. Source: analysis.mov_ave_spreads_detail (ma5_vs_ma60/120/255).';
COMMENT ON COLUMN analysis_forecasts.mov_pairs.forecast_id IS 'Surrogate PK + hash-partition key (1:N link to the bucket''s 5 period rows in analysis_forecasts.forecast_results, allocated by the writer, shared across all 5 periods). The bucket''s identity (sec_type, code, stat_month) + bucket family are registered in analysis_forecasts.forecast_identities under this id.';
COMMENT ON COLUMN analysis_forecasts.mov_pairs.pair_window IS 'Slow MA leg of the pair (trading days): 60 / 120 / 255. The fast leg is fixed at ma5; the cross is read off analysis.mov_ave_spreads_detail.ma5_vs_ma{pair_window}.';
COMMENT ON COLUMN analysis_forecasts.mov_pairs.side IS 'Bucket side: top = cross UP / golden cross (ma5_vs_ma{W}[t] > 0 and ma5_vs_ma{W}[t-1] <= 0 — the pair turns bullish; reversals are changes below the bucket''s FIXED 1% threshold (0.01 — the period-end n-day close vs ±1%); bottom = cross DOWN / death cross (spread turns < 0 from >= 0 — the pair turns bearish; reversals are changes above it).';
COMMENT ON COLUMN analysis_forecasts.mov_pairs.is_market_hyped IS 'TRUE when ANY of the bucket''s dates falls inside one of the code''s stats.mov_ave_market_hypes episodes (any min_checkin_period).';
COMMENT ON COLUMN analysis_forecasts.mov_pairs.lookback_period IS 'Recorded build parameter (NOT a PK member): the trailing calendar window the bucket was computed over — ''5y'' = (stat_month - 5 years, stat_month]. Default ''5y''; a rebuild with a different lookback requires --force.';
