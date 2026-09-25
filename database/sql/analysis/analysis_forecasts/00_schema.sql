-- ============================================================================
--  Schema: analysis_forecasts
--
--  Annual per-security FORECAST (forecast) analysis: one row per
--  (sec_type, code, stat_date, bucket) where stat_date is a snapshot
--  DATE on the ANNUAL grid (completed calendar YEAR-END). Each bucket
--  summarizes a TRAILING 10-YEAR
--  window (stat_date - 10 years, stat_date] of daily data for that
--  security — i.e. an annual snapshot of how the security's own extreme
--  days (RSI percentiles / Bollinger breaches) behaved over the window
--  and what the forward changes after those days looked like.
--
--  Layout (motivation / result split):
--    - 01_forecast_results.sql — analysis_forecasts.forecast_results:
--      the RESULT data only (mean / high / low forward changes for
--      next, 5d, 20d horizons), keyed by the surrogate forecast_id — plus
--      analysis_forecasts.forecast_identities: the shared-PK REGISTRY
--      (one row per forecast_id: sec_type, code, stat_date, bucket
--      family) the other forecast tables' leading PK columns are
--      migrated into, powering search by forecast_id.
--    - 02_mov_rsi_mov_std.sql — analysis_forecasts.mov_rsi and
--      analysis_forecasts.mov_std: the MOTIVATION (bucket-defining)
--      columns. Each motivation row carries a forecast_id that links to
--      its forecast_results row (1:1, indexed, NOT NULL). Also retires
--      the former mov_gap family (N-day price-return extreme-percentile
--      buckets — table dropped 2026-09, its registry + result rows
--      purged; the former 03_mov_gap.sql is deleted).
--      The former opp_pair_state family (industry opposite-pair trend
--      buckets, 07_opp_pair_state.sql) was likewise RETIRED 2026-09 —
--      table dropped, registry + result rows purged, the file deleted
--      (see the retirement block below).
--    - 04_base_rates.sql — analysis_forecasts.base_rates: the
--      UNCONDITIONAL same-window base rates (mean forward change over
--      all window days) the bucket results are read against.
--    - 10_high_low_streaks.sql — analysis_forecasts.high_low_streaks:
--      MA-Spread High/Low streak buckets — every band-break excursion
--      streak of analysis.mov_ave_high_low_pct_streaks audited at its
--      MEAN-MID anchor day (the ((day_count-1)//2 + 1)-th trading day
--      of the span; an 8-day streak anchors its 4th day — an EX-POST
--      audit anchor: the streak length is known only after the streak
--      closes).
--    - 11_pe_state.sql — analysis_forecasts.pe_state: valuation
--      extreme-percentile buckets over the PE series of analysis.pe
--      (raw PE, LOWER the better — the top-pct% (expensive) PE days
--      are bearish, side top; the mov_rsi pct convention);
--    - 12_dividend_state.sql — analysis_forecasts.dividend_state: the
--      sibling family over the dividend-yield series of
--      analysis.dividends (HIGHER the better — the top-pct%
--      (high-yield) days are bullish, side bottom; same pct grid as
--      pe_state, REVERSED side mapping). Each family's table carries
--      ONE metric — the combined pe_dividend_state's metric column is
--      gone (its rows migrate per metric into the two tables).
--
--  Population convention:
--    - `python -m analyze.analysis_forecasts` computes one snapshot per
--      completed YEAR (annual grid), incrementally (stat_dates missing
--      from the mov_* / base_rates tables are computed; the RUNNING
--      year — always the newest spec, keyed at its year-end but
--      computed only up to the latest available data date — plus the
--      most recent REFRESH_YEARS completed years are refreshed each
--      run: the forward-change RESULT half needs post-snapshot prices
--      that do not all exist yet when a snapshot is first written, so
--      its long-horizon occurrence counts fill in only on refresh).
--    - `--force` deletes the sec_type's mov_* rows (and their
--      forecast_results rows) and recomputes every target stat_date.
--    - Rows are emitted ONLY where the bucket day-count > 0 (empty
--      buckets — e.g. a code with no valid RSI in the window — have
--      no row in either table).
--
--  Change semantics (shared by all horizons):
--    next (1d) change    = (close[t+1] - close[t]) / close[t]
--    5d/20d change      = (close[t+N] - close[t]) / close[t]
--    (signed fractional ratios, e.g. 0.05 = +5%; computed per code on
--    its own trading-day sequence — calendar gaps do not count as rows)
--
--  Every table also carries a lookback_period column (TEXT, default
--  '10y' — a recorded build parameter, NOT a PK member) stating which
--  trailing calendar window the row was computed over, so a future
--  rebuild at a different lookback is self-describing.
--
--  (The swing-aware reversal probabilities reverse_prob /
--  base_down_prob / base_up_prob + their threshold bars were REMOVED
--  2026-09-25 — see 01_forecast_results.sql / 04_base_rates.sql and
--  analyze/analysis_forecasts/config/horizons.py.)
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS analysis_forecasts;

-- ----------------------------------------------------------------------------
--  2026-09-22 annual-grid migration (stat_month → stat_date, lookback
--  5y → 10y, snapshot cadence monthly → ANNUAL):
--    1. RENAME the real stat_month columns of the three tables that
--       carry it (forecast_identities, base_rates, regime_weights —
--       every other table reaches the date through forecast_id) —
--       idempotent DO blocks, skipped when stat_date already exists
--       (fresh installs create it directly). Indexes / PKs follow the
--       rename automatically.
--    2. Widen the recorded lookback defaults to '10y' on every table
--       carrying lookback_period (existing installs; fresh installs
--       create the default directly in each file).
--    3. PURGE the monthly-cadence rows of the whole schema: they sit
--       OFF the annual grid forever (the incremental logic only
--       touches annual stat_dates) and carry superseded 5y-lookback
--       semantics. The next run repopulates on the annual grid (one
--       snapshot per completed year-end + the running year, each over
--       a trailing 10-year window). analysis_signals' OWN tables are
--       untouched (their end_date months simply stop matching until
--       re-emitted against the annual buckets).
-- ----------------------------------------------------------------------------
DO $$
BEGIN    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_schema = 'analysis_forecasts'
                 AND table_name = 'forecast_identities'
                 AND column_name = 'stat_month') THEN
        ALTER TABLE analysis_forecasts.forecast_identities
            RENAME COLUMN stat_month TO stat_date;
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_schema = 'analysis_forecasts'
                 AND table_name = 'base_rates'
                 AND column_name = 'stat_month') THEN
        ALTER TABLE analysis_forecasts.base_rates
            RENAME COLUMN stat_month TO stat_date;
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_schema = 'analysis_forecasts'
                 AND table_name = 'regime_weights'
                 AND column_name = 'stat_month') THEN
        ALTER TABLE analysis_forecasts.regime_weights
            RENAME COLUMN stat_month TO stat_date;
    END IF;
    -- the fit-width parameter counts SNAPSHOTS on the annual grid (K
    -- prior year-ends), not months — 13_regime_weights.sql's fresh
    -- CREATE already carries the new name/default.
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_schema = 'analysis_forecasts'
                 AND table_name = 'regime_weights'
                 AND column_name = 'k_months') THEN
        ALTER TABLE analysis_forecasts.regime_weights
            RENAME COLUMN k_months TO k_snapshots;
        ALTER TABLE analysis_forecasts.regime_weights
            ALTER COLUMN k_snapshots SET DEFAULT 5;
    END IF;
END $$;

-- ----------------------------------------------------------------------------
--  Retired family: analysis_forecasts.opp_pair_state (former
--  07_opp_pair_state.sql — industry opposite-pair trend buckets).
--  Removed 2026-09: the family never emitted rows on the annual grid
--  and no signals engine consumes it; its legacy numpy pipeline (the
--  last consumer of the wide matrix machinery) went with it. Purge
--  the linked result + registry rows FIRST (the result delete resolves
--  (sec_type, bucket) through the registry), then DROP the table with
--  its 16 hash partitions (CASCADE — the mov_gap retirement precedent,
--  02_mov_rsi_mov_std.sql). Idempotent: no-ops once the table is gone.
-- ----------------------------------------------------------------------------

DELETE FROM analysis_forecasts.forecast_results f
USING analysis_forecasts.forecast_identities i
WHERE i.forecast_id = f.forecast_id AND i.bucket = 'opp_pair_state';

DELETE FROM analysis_forecasts.forecast_identities
WHERE bucket = 'opp_pair_state';

DROP TABLE IF EXISTS analysis_forecasts.opp_pair_state CASCADE;

ALTER TABLE analysis_forecasts.forecast_results  ALTER COLUMN lookback_period SET DEFAULT '10y';
ALTER TABLE analysis_forecasts.forecast_identities ALTER COLUMN lookback_period SET DEFAULT '10y';
ALTER TABLE analysis_forecasts.mov_rsi            ALTER COLUMN lookback_period SET DEFAULT '10y';
ALTER TABLE analysis_forecasts.mov_std            ALTER COLUMN lookback_period SET DEFAULT '10y';
ALTER TABLE analysis_forecasts.base_rates         ALTER COLUMN lookback_period SET DEFAULT '10y';
ALTER TABLE analysis_forecasts.margin_ratio_state ALTER COLUMN lookback_period SET DEFAULT '10y';
ALTER TABLE analysis_forecasts.mov_pairs          ALTER COLUMN lookback_period SET DEFAULT '10y';
ALTER TABLE analysis_forecasts.mov_pairs_ema      ALTER COLUMN lookback_period SET DEFAULT '10y';
ALTER TABLE analysis_forecasts.high_low_streaks   ALTER COLUMN lookback_period SET DEFAULT '10y';
ALTER TABLE analysis_forecasts.pe_state           ALTER COLUMN lookback_period SET DEFAULT '10y';
ALTER TABLE analysis_forecasts.dividend_state     ALTER COLUMN lookback_period SET DEFAULT '10y';

TRUNCATE analysis_forecasts.forecast_results,
         analysis_forecasts.forecast_identities,
         analysis_forecasts.base_rates,
         analysis_forecasts.mov_rsi,
         analysis_forecasts.mov_std,
         analysis_forecasts.margin_ratio_state,
         analysis_forecasts.mov_pairs,
         analysis_forecasts.mov_pairs_ema,
         analysis_forecasts.high_low_streaks,
         analysis_forecasts.pe_state,
         analysis_forecasts.dividend_state,
         analysis_forecasts.regime_weights;

COMMENT ON SCHEMA analysis_forecasts IS 'Annual per-security forecast analysis: motivation tables (mov_rsi / mov_std) hold the extreme-day bucket definitions per (sec_type, code, stat_date, bucket); forecast_results holds the forward-change result data keyed by forecast_id (1:1 with each motivation row); forecast_identities is the shared-PK registry (sec_type, code, stat_date, bucket family per forecast_id) powering search by forecast_id. Each row summarizes a trailing 10-year window of daily data at an annual snapshot cadence (stat_date = completed year-end). Populated incrementally by python -m analyze.analysis_forecasts.';
