-- ============================================================================
--  ALL pseudo-regime migration (2026-10-01)
--
--  Every (code, side, config, population) bucket of every forecast
--  family gains a 5th regime split regime_state = 'all' — the bucket
--  that takes ALL days indiscriminately (each day weight 1.0, no
--  re-weighting): the unconditioned reference the 4 regime splits
--  (calm/hot/panic/quiet — stats.market_regimes day labels) are read
--  against. On the streak families the all-days runs are re-merged
--  WITHOUT the regime key (a run spanning a regime flip is ONE signal
--  under 'all'); the one-day cross families just relabel. Fitted ONLY
--  to the forecast side: 'all' is NEVER a stats.market_regimes label,
--  the signals tier EXCLUDES it (signal_strategies /
--  history_signals stay 4-state — an 'all' strategy would duplicate
--  every regime strategy's live breaches on the same bars), and
--  regime_weights carries it as the CONSTANT weight 1.0 row per
--  (family, code) — the walk-forward fit never touches it.
--
--  This file widens the vocabulary CHECKs of the 4 motivation tables
--  + regime_weights for EXISTING installs (fresh installs already
--  carry the widened CHECKs from their CREATE TABLE files) and
--  re-stamps the affected column comments. Fully idempotent: a
--  constraint whose definition already admits 'all' is left alone.
--
--  Applied by temp_scripts/_apply_all_regime_split.py. The bucket
--  rows themselves are backfilled by the analysis_forecasts pipeline's
--  'all'-presence backfill leg (writer._compute_dates — present
--  snapshots with no 'all' rows are deleted and recomputed, the same
--  mechanism as the 2026-09-27 ohlcv population backfill).
-- ============================================================================

DO $$
DECLARE
    t record;
    def text;
    has_all boolean;
    missing boolean;
BEGIN
    FOR t IN
        SELECT * FROM (VALUES
            ('analysis_forecasts.mov_rsi',       'ck_mov_rsi_regime',
             'regime_state'),
            ('analysis_forecasts.mov_std',       'ck_mov_std_regime',
             'regime_state'),
            ('analysis_forecasts.mov_pairs',     'ck_mov_pairs_regime',
             'regime_state'),
            ('analysis_forecasts.mov_pairs_ema', 'ck_mov_pairs_ema_regime',
             'regime_state'),
            ('analysis_forecasts.regime_weights','ck_regime_weights_regime',
             'regime')
        ) AS v(tbl, con, col)
    LOOP
        SELECT pg_get_constraintdef(c.oid)
        INTO def
        FROM pg_constraint c
        WHERE c.conrelid = t.tbl::regclass
          AND c.conname = t.con
          AND c.contype = 'c';

        has_all := def IS NOT NULL AND position('all' in def) > 0;
        missing := def IS NULL;

        -- a stale 4-state constraint (or none at all): re-apply widened.
        -- NOT VALID first (metadata-only, never blocks concurrent COPY),
        -- then VALIDATE — pre-migration rows are all 4-state, so the
        -- scan passes; the constraint recurses to the hash partitions.
        IF NOT has_all THEN
            IF NOT missing THEN
                EXECUTE format('ALTER TABLE %s DROP CONSTRAINT %I',
                               t.tbl, t.con);
            END IF;
            EXECUTE format(
                'ALTER TABLE %s ADD CONSTRAINT %I CHECK (%I IN '
                '(''calm'', ''hot'', ''panic'', ''quiet'', ''all'')) '
                'NOT VALID', t.tbl, t.con, t.col);
            EXECUTE format('ALTER TABLE %s VALIDATE CONSTRAINT %I',
                           t.tbl, t.con);
            RAISE NOTICE 'widened %.% -> admits ''all''', t.tbl, t.con;
        END IF;
    END LOOP;
END $$;

-- ----------------------------------------------------------------------------
--  Column comments — re-stamped with the 'all' vocabulary
-- ----------------------------------------------------------------------------
COMMENT ON COLUMN analysis_forecasts.mov_rsi.regime_state IS 'The bucket''s market-regime split: the stats.market_regimes day label (calm / hot / panic / quiet) carried by the bucket''s trigger days — every trigger day joins exactly one regime, so each (config, regime) pair is its own bucket — PLUS the ALL pseudo-regime ''all'': the 5th split per (code, side, config, population) whose population is ALL qualifying days indiscriminately (each day weight 1.0 — the unconditioned reference the 4 regime splits are read against; streaks re-merged WITHOUT the regime key, so a run spanning a regime flip is ONE signal). Never a stats.market_regimes label; the signals tier excludes it.';
COMMENT ON COLUMN analysis_forecasts.mov_std.regime_state IS 'The bucket''s market-regime split: the stats.market_regimes day label (calm / hot / panic / quiet) carried by the bucket''s breach days — every breach day joins exactly one regime, so each (config, regime) pair is its own bucket — PLUS the ALL pseudo-regime ''all'': the 5th split per (code, side, config, population) whose population is ALL qualifying days indiscriminately (each day weight 1.0 — the unconditioned reference the 4 regime splits are read against; streaks re-merged WITHOUT the regime key, so a run spanning a regime flip is ONE signal). Never a stats.market_regimes label; the signals tier excludes it.';
COMMENT ON COLUMN analysis_forecasts.mov_pairs.regime_state IS 'The bucket''s market-regime split: the stats.market_regimes day label (calm / hot / panic / quiet) carried by the bucket''s cross days — every cross day joins exactly one regime, so each (config, regime) pair is its own bucket — PLUS the ALL pseudo-regime ''all'': the 5th split per (code, side, config, population) whose population is ALL qualifying days indiscriminately (one-day signals — the ''all'' span is the same days re-labeled). Never a stats.market_regimes label; the signals tier excludes it.';
COMMENT ON COLUMN analysis_forecasts.mov_pairs_ema.regime_state IS 'The bucket''s market-regime split: the stats.market_regimes day label (calm / hot / panic / quiet) carried by the bucket''s cross days — every cross day joins exactly one regime, so each (config, regime) pair is its own bucket — PLUS the ALL pseudo-regime ''all'': the 5th split per (code, side, config, population) whose population is ALL qualifying days indiscriminately (one-day signals — the ''all'' span is the same days re-labeled). Never a stats.market_regimes label; the signals tier excludes it.';
COMMENT ON COLUMN analysis_forecasts.regime_weights.regime IS 'The regime the weight multiplies: calm / hot / panic / quiet (stats.market_regimes — the walk-forward fitted rows) or ''all'' (the ALL pseudo-regime — the CONSTANT weight 1.0 row per (family, code): the unconditioned full-population bucket takes all days indiscriminately, no fit). The 4 fitted regimes'' weights sum to ~1 per (family, code); the ''all'' row stands outside that normalization.';
