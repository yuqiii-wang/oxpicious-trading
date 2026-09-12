-- ============================================================================
--  Tables: analysis_forecasts.mov_rsi + analysis_forecasts.mov_std
--
--  The MOTIVATION (bucket-defining) tables of the forecast analysis.
--  Each row identifies ONE extreme-day bucket within the trailing
--  5-year window (stat_month - 5y, stat_month] of the code's own
--  trading days, stores the bucket's motivation stats, and links via
--  forecast_id to its RESULT rows in analysis_forecasts.forecast_results
--  (1:N — one forecast_id → 4 period rows: next / 5d / 20d / 60d).
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
--  columns below (rsi_window / side / pct / is_market_hyped) are plain
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
--        6 / 10 / 14 (classic Wilder) / 20 / 60 days.
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
--    is_market_hyped (both) — TRUE when ANY of the bucket's
--                  dates falls inside one of the code's
--                  stats.mov_ave_market_hypes episodes (any
--                  min_checkin_period). The underlying RSI / band values
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
--  Table: analysis_forecasts.mov_rsi
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS analysis_forecasts.mov_rsi (
    code            TEXT         NOT NULL,  -- hash partition key + PK lead; sec_type / stat_month live in forecast_identities
    forecast_id     BIGINT       NOT NULL,  -- 1:N link to the bucket's 4 forecast_results period rows; id-only joins/searches use idx_mov_rsi_forecast_id
    rsi_window      INTEGER      NOT NULL,  -- RSI window in trading days: 6/10/14/20/60
    side            TEXT         NOT NULL,  -- 'top' (overbought) | 'bottom' (oversold)
    pct             INTEGER      NOT NULL,  -- percentile width: 1 / 5 / 10 / 25

    -- recorded build parameter (NOT PK): trailing window the bucket was
    -- computed over — '5y' = (stat_month - 5y, stat_month]
    lookback_period TEXT         NOT NULL DEFAULT '5y',

    -- motivation cols
    is_market_hyped BOOLEAN      NOT NULL,  -- ANY bucket date inside a mov_ave_market_hypes episode (any check-in period)

    CONSTRAINT pk_mov_rsi PRIMARY KEY (code, forecast_id)
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
    is_market_hyped  BOOLEAN      NOT NULL,  -- ANY breach date inside a mov_ave_market_hypes episode (any check-in period)

    CONSTRAINT pk_mov_std PRIMARY KEY (code, forecast_id)
) PARTITION BY HASH (code);

SELECT public.create_hash_partitions('analysis_forecasts', 'mov_std', 16);

-- ----------------------------------------------------------------------------
--  Migration (2026-09): (code, forecast_id) PK on HASH (code) partitions
--  — the code-clustered read/write axis. Handles BOTH pre-migration
--  shapes:
--    - composite-PK tables (code, sec_type, stat_month, ..., is_market_hyped)
--      with HASH (code): drops the identity cols sec_type / stat_month
--      (code stays — the partition key) and re-keys by (code, forecast_id);
--    - the intermediate forecast_id-keyed shape (PK (forecast_id), HASH
--      (forecast_id), no code): re-adds code by joining
--      forecast_identities (code is registered there per forecast_id).
--  Rows are carried over via forecast_id in both branches; the linked
--  forecast_results / forecast_identities rows stay valid. Guarded by
--  shape detection — no-op on fresh installs and after the migration.
-- ----------------------------------------------------------------------------
DO $$
DECLARE
    r          int;
    v_partkey  text;
    v_pkdef    text;
    v_has_code bool;
BEGIN
    -- ---- mov_rsi ------------------------------------------------------
    SELECT pg_get_partkeydef(c.oid),
           COALESCE((SELECT pg_get_constraintdef(p.oid)
                     FROM pg_constraint p
                     WHERE p.conrelid = c.oid AND p.contype = 'p'), '')
    INTO v_partkey, v_pkdef
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'analysis_forecasts' AND c.relname = 'mov_rsi';
    SELECT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_schema = 'analysis_forecasts'
                     AND table_name   = 'mov_rsi'
                     AND column_name  = 'code')
    INTO v_has_code;
    IF v_partkey IS NOT NULL
       AND (v_partkey <> 'HASH (code)'
            OR v_pkdef <> 'PRIMARY KEY (code, forecast_id)') THEN
        ALTER TABLE analysis_forecasts.mov_rsi RENAME TO mov_rsi_pk_rebuild;
        -- free the pk index name for the new table
        ALTER TABLE analysis_forecasts.mov_rsi_pk_rebuild
            DROP CONSTRAINT IF EXISTS pk_mov_rsi;
        CREATE TABLE analysis_forecasts.mov_rsi_new (
            code            TEXT         NOT NULL,
            forecast_id     BIGINT       NOT NULL,
            rsi_window      INTEGER      NOT NULL,
            side            TEXT         NOT NULL,
            pct             INTEGER      NOT NULL,
            lookback_period TEXT         NOT NULL DEFAULT '5y',
            is_market_hyped BOOLEAN      NOT NULL,
            CONSTRAINT pk_mov_rsi PRIMARY KEY (code, forecast_id)
        ) PARTITION BY HASH (code);
        PERFORM public.create_hash_partitions('analysis_forecasts',
                                              'mov_rsi_new', 16);
        -- normalize older shapes before the carry-over
        ALTER TABLE analysis_forecasts.mov_rsi_pk_rebuild
            ADD COLUMN IF NOT EXISTS lookback_period TEXT NOT NULL DEFAULT '5y';
        IF v_has_code THEN
            INSERT INTO analysis_forecasts.mov_rsi_new
                   (code, forecast_id, rsi_window, side, pct,
                    lookback_period, is_market_hyped)
            SELECT  code, forecast_id, rsi_window, side, pct,
                    lookback_period, is_market_hyped
            FROM    analysis_forecasts.mov_rsi_pk_rebuild;
        ELSE
            -- intermediate forecast_id-keyed shape (no code column):
            -- code comes from the identities registry
            INSERT INTO analysis_forecasts.mov_rsi_new
                   (code, forecast_id, rsi_window, side, pct,
                    lookback_period, is_market_hyped)
            SELECT  i.code, m.forecast_id, m.rsi_window, m.side, m.pct,
                    m.lookback_period, m.is_market_hyped
            FROM    analysis_forecasts.mov_rsi_pk_rebuild m
            JOIN    analysis_forecasts.forecast_identities i
              ON    i.forecast_id = m.forecast_id;
        END IF;
        DROP TABLE analysis_forecasts.mov_rsi_pk_rebuild;
        ALTER TABLE analysis_forecasts.mov_rsi_new RENAME TO mov_rsi;
        -- restore the create_hash_partitions child-name convention
        FOR r IN 0..15 LOOP
            EXECUTE format(
                'ALTER TABLE analysis_forecasts.mov_rsi_new_p%s '
                'RENAME TO mov_rsi_p%s',
                lpad(r::text, 2, '0'), lpad(r::text, 2, '0'));
        END LOOP;
    END IF;

    -- ---- mov_std ------------------------------------------------------
    SELECT pg_get_partkeydef(c.oid),
           COALESCE((SELECT pg_get_constraintdef(p.oid)
                     FROM pg_constraint p
                     WHERE p.conrelid = c.oid AND p.contype = 'p'), '')
    INTO v_partkey, v_pkdef
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'analysis_forecasts' AND c.relname = 'mov_std';
    SELECT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_schema = 'analysis_forecasts'
                     AND table_name   = 'mov_std'
                     AND column_name  = 'code')
    INTO v_has_code;
    IF v_partkey IS NOT NULL
       AND (v_partkey <> 'HASH (code)'
            OR v_pkdef <> 'PRIMARY KEY (code, forecast_id)') THEN
        ALTER TABLE analysis_forecasts.mov_std RENAME TO mov_std_pk_rebuild;
        ALTER TABLE analysis_forecasts.mov_std_pk_rebuild
            DROP CONSTRAINT IF EXISTS pk_mov_std;
        CREATE TABLE analysis_forecasts.mov_std_new (
            code            TEXT         NOT NULL,
            forecast_id     BIGINT       NOT NULL,
            ma_window       INTEGER      NOT NULL,
            k               NUMERIC(4,2) NOT NULL,
            side            TEXT         NOT NULL,
            lookback_period TEXT         NOT NULL DEFAULT '5y',
            is_market_hyped BOOLEAN      NOT NULL,
            CONSTRAINT pk_mov_std PRIMARY KEY (code, forecast_id)
        ) PARTITION BY HASH (code);
        PERFORM public.create_hash_partitions('analysis_forecasts',
                                              'mov_std_new', 16);
        ALTER TABLE analysis_forecasts.mov_std_pk_rebuild
            ADD COLUMN IF NOT EXISTS lookback_period TEXT NOT NULL DEFAULT '5y';
        IF v_has_code THEN
            INSERT INTO analysis_forecasts.mov_std_new
                   (code, forecast_id, ma_window, k, side,
                    lookback_period, is_market_hyped)
            SELECT  code, forecast_id, ma_window, k, side,
                    lookback_period, is_market_hyped
            FROM    analysis_forecasts.mov_std_pk_rebuild;
        ELSE
            INSERT INTO analysis_forecasts.mov_std_new
                   (code, forecast_id, ma_window, k, side,
                    lookback_period, is_market_hyped)
            SELECT  i.code, m.forecast_id, m.ma_window, m.k, m.side,
                    m.lookback_period, m.is_market_hyped
            FROM    analysis_forecasts.mov_std_pk_rebuild m
            JOIN    analysis_forecasts.forecast_identities i
              ON    i.forecast_id = m.forecast_id;
        END IF;
        DROP TABLE analysis_forecasts.mov_std_pk_rebuild;
        ALTER TABLE analysis_forecasts.mov_std_new RENAME TO mov_std;
        FOR r IN 0..15 LOOP
            EXECUTE format(
                'ALTER TABLE analysis_forecasts.mov_std_new_p%s '
                'RENAME TO mov_std_p%s',
                lpad(r::text, 2, '0'), lpad(r::text, 2, '0'));
        END LOOP;
    END IF;
END $$;

-- forecast_id-only lookups (search-by-id, the gate's registry join) —
-- the PK leads with code, so an id-only predicate needs its own index
CREATE INDEX IF NOT EXISTS idx_mov_rsi_forecast_id
    ON analysis_forecasts.mov_rsi (forecast_id);

CREATE INDEX IF NOT EXISTS idx_mov_std_forecast_id
    ON analysis_forecasts.mov_std (forecast_id);

-- ----------------------------------------------------------------------------
--  Comments — mov_rsi
-- ----------------------------------------------------------------------------
COMMENT ON TABLE analysis_forecasts.mov_rsi IS 'RSI extreme-day bucket definitions (motivation): one row per forecast_id — the days of one security-month whose rsi_{W}days is in the top/bottom pct% of the trailing 5-year window ending at the bucket''s stat_month, streak-merged (2026-09: consecutive bucket days are ONE forecast signal anchored at the run''s MID day — the mean run length per signal lives on forecast_identities.streak_signal_days; the legacy fixed-5-day cooldown was removed), split by whether any bucket date is a market-hyped date. PK (code, forecast_id) on HASH (code) partitions — code-clustered reads/writes; the identity (sec_type, stat_month) + bucket family live in analysis_forecasts.forecast_identities. Results (forward changes / reversal probabilities) live in analysis_forecasts.forecast_results via forecast_id. Bucket day RSI values join from analysis.mov_ave_rsi on (sec_type, code, date, rsi_window). Source: analysis.mov_ave_rsi + stats.*_basic_stats closes.';
COMMENT ON COLUMN analysis_forecasts.mov_rsi.code IS 'Hash partition key + PK lead. The bucket''s sec_type and stat_month are registered in analysis_forecasts.forecast_identities under the row''s forecast_id.';
COMMENT ON COLUMN analysis_forecasts.mov_rsi.forecast_id IS 'Surrogate id (PK partner of code; 1:N link to the bucket''s 4 period rows in analysis_forecasts.forecast_results, allocated by the writer, shared across all 4 periods). The bucket''s identity (sec_type, stat_month) + bucket family are registered in analysis_forecasts.forecast_identities under this id; id-only lookups use idx_mov_rsi_forecast_id.';
COMMENT ON COLUMN analysis_forecasts.mov_rsi.rsi_window IS 'RSI window (trading days) whose extreme days are bucketed: 6/10/14/20/60 — mirrors analysis.mov_ave_rsi.rsi_*days.';
COMMENT ON COLUMN analysis_forecasts.mov_rsi.side IS 'Bucket side: top = rsi in the top pct% of the window (overbought; reversals are changes below the bucket''s FIXED 1% reverse_threshold (0.01 — the period-end n-day close vs ±1%); bottom = rsi in the bottom pct% (oversold; reversals are changes above it).';
COMMENT ON COLUMN analysis_forecasts.mov_rsi.pct IS 'Percentile width of the bucket: 1, 5, 10 or 25 (percent). The threshold is the window''s (linear-interpolated) percentile of rsi_{W}days over non-NULL values.';
COMMENT ON COLUMN analysis_forecasts.mov_rsi.is_market_hyped IS 'TRUE when ANY of the bucket''s dates falls inside one of the code''s stats.mov_ave_market_hypes episodes (any min_checkin_period).';
COMMENT ON COLUMN analysis_forecasts.mov_rsi.lookback_period IS 'Recorded build parameter (NOT a PK member): the trailing calendar window the bucket was computed over — ''5y'' = (stat_month - 5 years, stat_month]. Default ''5y''; a rebuild with a different lookback requires --force.';

-- ----------------------------------------------------------------------------
--  Comments — mov_std
-- ----------------------------------------------------------------------------
COMMENT ON TABLE analysis_forecasts.mov_std IS 'Bollinger-breach bucket definitions (motivation): one row per forecast_id — the days of one security-month within the trailing 5-year window ending at the bucket''s stat_month whose price closed beyond ma_{W} ± k·std_{W}days, streak-merged (2026-09: consecutive breach days are ONE forecast signal anchored at the run''s MID day — the mean run length per signal lives on forecast_identities.streak_signal_days; the legacy fixed-5-day cooldown was removed). PK (code, forecast_id) on HASH (code) partitions — code-clustered reads/writes; the identity (sec_type, stat_month) + bucket family live in analysis_forecasts.forecast_identities. Results (forward changes / reversal probabilities) live in forecast_results linked via forecast_id. Band inputs join from analysis.mov_ave_spreads_detail / stats.*_tech_stats. Sources: stats.*_tech_stats (ma), analysis.mov_ave_spreads_detail (std), stats.*_basic_stats closes (price, COALESCE etf_adjustment.adj_close for ETFs).';
COMMENT ON COLUMN analysis_forecasts.mov_std.code IS 'Hash partition key + PK lead. The bucket''s sec_type and stat_month are registered in analysis_forecasts.forecast_identities under the row''s forecast_id.';
COMMENT ON COLUMN analysis_forecasts.mov_std.forecast_id IS 'Surrogate id (PK partner of code; 1:N link to the bucket''s 4 period rows in analysis_forecasts.forecast_results, allocated by the writer, shared across all 4 periods). The bucket''s identity (sec_type, stat_month) + bucket family are registered in analysis_forecasts.forecast_identities under this id; id-only lookups use idx_mov_std_forecast_id.';
COMMENT ON COLUMN analysis_forecasts.mov_std.ma_window IS 'MA/σ window (trading days): 5/20/60 — ma_{W} from stats.*_tech_stats, std_{W}days from analysis.mov_ave_spreads_detail.';
COMMENT ON COLUMN analysis_forecasts.mov_std.k IS 'σ multiple defining the Bollinger bound: 0.5 / 1.0 / 1.5 / 2.0 / 2.5 / 3.0.';
COMMENT ON COLUMN analysis_forecasts.mov_std.side IS 'Breach side: upper = price > ma_{W} + k·std_{W}days (reversals are changes below the bucket''s FIXED 1% reverse_threshold (0.01 — the period-end n-day close vs ±1%); lower = price < ma_{W} - k·std_{W}days (reversals are changes above it).';
COMMENT ON COLUMN analysis_forecasts.mov_std.is_market_hyped IS 'TRUE when ANY breach date falls inside one of the code''s stats.mov_ave_market_hypes episodes (any min_checkin_period).';
COMMENT ON COLUMN analysis_forecasts.mov_std.lookback_period IS 'Recorded build parameter (NOT a PK member): the trailing calendar window the bucket was computed over — ''5y'' = (stat_month - 5 years, stat_month]. Default ''5y''; a rebuild with a different lookback requires --force.';
