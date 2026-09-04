-- ============================================================================
--  Recurring Cycles Analysis — per-(sec_type, code, last_date, range_days)
--  recurring rise/drop periodicity of close prices.
--
--  Replaces the former analysis.fourier_freqs (dominant FFT-frequency
--  design). The FFT's amplitude argmax was an IMPLICATION of periodicity,
--  frequently misleading for the stated purpose (recurring rise/drop
--  periodicity): a single swing, a trend, or a harmonic could dominate
--  the spectrum without any actual recurrence. This design re-grounds the
--  claim in the TIME DOMAIN: every integer day period d (2..N/2) is
--  audited for RECURRENCE — price actually cycling up-and-down with
--  spacing ≈ d — and the amplitude only GATES the result.
--
--  For each security (index / etf / stock) and each trading date
--  (last_date), takes the trailing `range_days` close prices as a window
--  and computes per integer day period d:
--
--    amplitude(d)  — energy-merged FFT amplitude of the day
--                    (sqrt(Σ amp_k²) over bins k rounding to d). The
--                    Fourier REFERENCE (yuan), NOT recurrence evidence.
--    count(d)      = recEXT(d) × acfFrac(d) — the recurrence COUNT
--                    factor: prominence-filtered alternating-extrema
--                    evidence (swings within ±15% of d over the max
--                    possible cycles floor((N−d)/d), capped 1) ×
--                    MA-detrended ACF coherence (fraction of multiples
--                    m·d with biased acf ≥ 1.96/√N). 0 when the price
--                    never actually repeated that spacing (one-off
--                    swings, trends, noise).
--    strength(d)   = (amplitude(d) / σ_band) × count(d) — the summarized
--                    recurring strength, 0 where not auditable (d > N/3
--                    — under 3 cycles in the window).
--    significance(d) — the POISSON AUDIT (added 2026-09): the raw
--                    extrema-pool hit count hits(d) (the recEXT
--                    numerator) is tested against the chance expectation
--                    λ̂₀(d) of a point-process null, empirically
--                    calibrated per (N, d, pool size) on random-walk +
--                    stochastic-vol nulls; p = P(Poisson(λ̂₀) ≥ hits),
--                    Bonferroni-adjusted over the auditable days (N/3−1)
--                    and stored as −log10(p) — 0 = not significant,
--                    ≥ 1.30 ⇔ p < 0.05. Validated: FPR ≤ 5% on synthetic
--                    nulls at every range_days, power 99%+ on 20d cycles.
--
--  Table: analysis.recurring_cycles
--    PK: (code, sec_type, last_date, range_days)
--    sec_type ∈ ('index' | 'etf' | 'stock')
--    range_days ∈ (20, 60, 255, 500, 750, 1275)
--
--  COLUMNS
--    period_days           — THE HEADLINE: the recurring rise/drop
--                            period in trading days = argmax of
--                            strength_spectrum (+2 day offset). 0 = NO
--                            recurring period detected (all strengths
--                            0: flat window, pure trend, or one-off
--                            swings — count gates them out).
--
--    strength              — strength(d*) at period_days d* (0 when
--                            period_days = 0). Kept as a scalar so the
--                            lightweight codes/chart endpoints don't
--                            have to scan the arrays.
--
--    count_factor          — count(d*) at period_days d* — the raw
--                            recurrence evidence behind the headline.
--
--    amplitude             — energy-merged FFT amplitude (yuan) at
--                            period_days d* — the swing size of the
--                            recurring cycle.
--
--    amplitude_spectrum    — Per-day energy-merged FFT amplitude (yuan),
--                            Postgres double-precision array. DAY-ALIGNED:
--                            element j (0-based) is day period d = j + 2.
--                            Length = floor(range_days/2) − 1 (days
--                            2..N/2; day 1 = DC is excluded by
--                            construction). Drives the amp bars of the
--                            per-date recurring-cycle bar charts on the
--                            Recurring Cycles page (one chart per
--                            range_days window, reactive to a clicked
--                            date on the top index price plot).
--
--    count_spectrum        — The recurrence COUNT factor per day
--                            (day-aligned like amplitude_spectrum:
--                            element j = day j+2). Says WHETHER the
--                            price actually repeated that spacing.
--
--    strength_spectrum     — The summarized recurring STRENGTH per day
--                            (day-aligned): (amplitude/σ_band) × count,
--                            0 for d > N/3. period_days = argmax + 2.
--
--    significance          — −log10 of the Bonferroni-adjusted Poisson
--                            p-value at period_days d* — how far the
--                            observed swing-hit count exceeds the chance
--                            expectation λ̂₀ (0 = not significant;
--                            ≥ 1.30 ⇔ p < 0.05). 0 when period_days = 0.
--
--    evidence_ratio        — hits(d*)/λ̂₀(d*): observed prominence-
--                            filtered swing-hit count over the Poisson
--                            null expectation at the headline period.
--                            0 when period_days = 0.
--
--    significance_spectrum — Per-day −log10 Bonferroni Poisson p-value
--                            (day-aligned like amplitude_spectrum:
--                            element j = day j+2), 0 for d > N/3 (not
--                            auditable) and capped at 300. Drives the
--                            significance bars of the per-date charts.
--
--    hits_spectrum         — Per-day OBSERVED swing-hit count (day-
--                            aligned: element j = day j+2), the recEXT
--                            numerator (uncapped integral count). The
--                            observed side of the Poisson audit —
--                            drives the Poisson audit table's "hits"
--                            column alongside lam0_spectrum.
--
--    lam0_spectrum         — Per-day chance expectation λ̂₀(d) of the
--                            point-process null (day-aligned): the
--                            calibrated n_pool × g(bin, day) rate the
--                            observed hits are tested against. The
--                            expected side of the Poisson audit.
--
--    last_date             — Last trading date of the sliding window.
--                            The window covers the `range_days` trading
--                            days ending on (and including) last_date.
--
--    range_days            — Window size in trading days. Constrained to
--                            (20, 60, 255, 500, 750, 1275) — short-term,
--                            monthly, yearly, 2-year, 3-year, and 5-year
--                            windows respectively.
--
--  SOURCE
--    Close prices from stats schema:
--      index -> stats.index_basic_stats.close
--      etf   -> COALESCE(stats.etf_adjustment.adj_close,
--                         stats.etf_basic_stats.close)
--               (adjusted close preferred — removes dividend/split jumps
--                that would create spurious frequency components)
--      stock -> stats.stock_basic_stats.close
--
--  POPULATION
--    analyze.recurring_cycles (Python module, truncate-then-recompute
--    per sec_type on --force; incremental missing-date upsert otherwise).
--    Per project rule, ALL INSERTs are in Python — no raw INSERT...SELECT
--    SQL in this file.
--
--  Register in analysis.analysis_identity (name='recurring_cycles').
--  The former analysis.fourier_freqs table and its identity row are
--  dropped (semantics changed — rows are not comparable).
-- ============================================================================
DROP TABLE IF EXISTS analysis.fourier_freqs CASCADE;
DELETE FROM analysis.analysis_identity WHERE name = 'fourier_freqs';

CREATE TABLE IF NOT EXISTS analysis.recurring_cycles (
    sec_type        TEXT         NOT NULL,  -- 'index' | 'etf' | 'stock'
    code            TEXT         NOT NULL,

    period_days     INTEGER      NOT NULL,  -- recurring rise/drop period in DAYS; 0 = none
    strength        NUMERIC(20, 10) NOT NULL,
    count_factor    NUMERIC(20, 10) NOT NULL,
    amplitude       NUMERIC(20, 10) NOT NULL,
    -- Poisson audit at period_days d*: −log10 Bonferroni p of the
    -- extrema-hit count vs the calibrated chance rate λ̂₀
    -- (0 = not significant; ≥ 1.30 ⇔ p < 0.05).
    significance    NUMERIC(20, 10) NOT NULL DEFAULT 0,
    -- hits(d*)/λ̂₀(d*) — observed swing-hit count over chance expectation.
    evidence_ratio  NUMERIC(20, 10) NOT NULL DEFAULT 0,
    -- Per-day spectra, DAY-ALIGNED: element j = integer day period j+2.
    -- Length = floor(range_days/2) − 1 (days 2..N/2).
    amplitude_spectrum          DOUBLE PRECISION[],
    count_spectrum              DOUBLE PRECISION[],
    strength_spectrum           DOUBLE PRECISION[],
    -- Per-day −log10 Bonferroni Poisson p (0 where not auditable
    -- d > N/3 / not significant; capped at 300).
    significance_spectrum       DOUBLE PRECISION[],
    -- Per-day OBSERVED swing-hit count (day-aligned: element j = day
    -- j+2) — the recEXT numerator, uncapped. Observed side of the
    -- Poisson audit table.
    hits_spectrum               DOUBLE PRECISION[],
    -- Per-day chance expectation λ̂₀(d) of the point-process null
    -- (day-aligned) — the calibrated n_pool × g(bin, day) rate.
    lam0_spectrum               DOUBLE PRECISION[],

    last_date       DATE         NOT NULL,
    range_days      INTEGER      NOT NULL,  -- window size in trading days

    CONSTRAINT pk_recurring_cycles PRIMARY KEY (code, sec_type, last_date, range_days),
    CONSTRAINT chk_recurring_cycles_sec_type
        CHECK (sec_type IN ('index', 'etf', 'stock')),
    CONSTRAINT chk_recurring_cycles_range_days
        CHECK (range_days IN (20, 60, 255, 500, 750, 1275))
) PARTITION BY HASH (code);

-- Native hash partitions (32) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p31
SELECT public.create_hash_partitions('analysis', 'recurring_cycles', 32);

-- Poisson-audit columns (added 2026-09) — idempotent backfill for
-- deployments created before the audit existed; fresh installs already
-- get them from the CREATE TABLE above. Adding a column with a constant
-- default on a partitioned parent propagates to all 32 partitions
-- without a rewrite (PostgreSQL fast-default).
ALTER TABLE analysis.recurring_cycles
    ADD COLUMN IF NOT EXISTS significance   NUMERIC(20, 10) NOT NULL DEFAULT 0;
ALTER TABLE analysis.recurring_cycles
    ADD COLUMN IF NOT EXISTS evidence_ratio NUMERIC(20, 10) NOT NULL DEFAULT 0;
ALTER TABLE analysis.recurring_cycles
    ADD COLUMN IF NOT EXISTS significance_spectrum DOUBLE PRECISION[];
ALTER TABLE analysis.recurring_cycles
    ADD COLUMN IF NOT EXISTS hits_spectrum DOUBLE PRECISION[];
ALTER TABLE analysis.recurring_cycles
    ADD COLUMN IF NOT EXISTS lam0_spectrum DOUBLE PRECISION[];

-- Indexes for common access patterns:
--   1. Per-date snapshot (drives the latest-date cross-sectional view).
--   2. sec_type-scoped scan.
-- Per-code lookups are served by the code-first PK.
CREATE INDEX IF NOT EXISTS idx_recurring_cycles_last_date
    ON analysis.recurring_cycles (last_date);
CREATE INDEX IF NOT EXISTS idx_recurring_cycles_sec_type_last_date
    ON analysis.recurring_cycles (sec_type, last_date);

COMMENT ON TABLE  analysis.recurring_cycles                  IS 'Per-(code, sec_type, last_date, range_days) recurring rise/drop periodicity of close prices. For each security and trading date, takes the trailing range_days close prices and audits every integer day period d (2..N/2) for RECURRENCE in the time domain: count(d) = prominence-filtered alternating-extrema evidence (swings within ±15% of d) × MA-detrended ACF coherence (multiples m·d with biased acf ≥ 1.96/√N); strength(d) = (amp(d)/σ_band) × count(d) where amp(d) is the energy-merged FFT amplitude (Fourier reference). Headline period_days = argmax of strength (0 = no recurring period — one-off swings/trends gated out by count). Per-day spectra stored day-aligned (element j = day j+2, length floor(range_days/2)−1). FFT used ONLY as the amplitude reference and to compute the ACF (Wiener–Khinchin). POISSON AUDIT: the raw extrema-pool hit count hits(d) is tested against the chance expectation λ̂₀(d) of a point-process null, empirically calibrated per (N, d, pool size) on random-walk + stochastic-vol nulls (poisson_calibration.json); significance(d) = −log10 of the Bonferroni p-value P(Poisson(λ̂₀) ≥ hits) over the auditable days (d ≤ N/3) — 0 = not significant, ≥ 1.30 ⇔ p < 0.05 (validated FPR ≤ 5% on nulls, power 99%+ on synthetic 20d cycles); evidence_ratio = hits(d*)/λ̂₀(d*) at the headline period; significance_spectrum day-aligned like the other spectra. range_days constrained to (20, 60, 255, 500, 750, 1275). Source: index=index_basic_stats.close, etf=COALESCE(etf_adjustment.adj_close, etf_basic_stats.close), stock=stock_basic_stats.close. Built by analyze.recurring_cycles (truncate-then-recompute per sec_type on --force; incremental missing-date upsert otherwise); all INSERTs in Python per project rule. Replaces analysis.fourier_freqs (dropped).';
COMMENT ON COLUMN analysis.recurring_cycles.sec_type          IS 'Subject security type: index, etf, or stock.';
COMMENT ON COLUMN analysis.recurring_cycles.code              IS 'Security code (bare index code e.g. 000300; ETF/stock ticker with exchange suffix e.g. 159001.SZ / 600008.SS).';
COMMENT ON COLUMN analysis.recurring_cycles.period_days       IS 'THE HEADLINE: recurring rise/drop period in trading days = argmax of strength_spectrum (+2 day offset). Minimum 2 (Nyquist); maximum range_days/3 (auditable — at least 3 cycles in the window). 0 = no recurring period detected (flat window, pure trend, or one-off swings — count gates them out).';
COMMENT ON COLUMN analysis.recurring_cycles.strength           IS 'strength(d*) at period_days d*: (amplitude(d*)/σ_band) × count(d*) — the summarized recurring strength. 0 when period_days = 0. Kept as a scalar so lightweight endpoints do not scan the arrays.';
COMMENT ON COLUMN analysis.recurring_cycles.count_factor       IS 'count(d*) at period_days d*: recEXT(d*) × acfFrac(d*) — the raw recurrence evidence (extrema hits × significant ACF multiples) behind the headline period.';
COMMENT ON COLUMN analysis.recurring_cycles.amplitude          IS 'Energy-merged FFT amplitude (yuan) at period_days d* — the swing size of the recurring rise/drop cycle. 0 when period_days = 0.';
COMMENT ON COLUMN analysis.recurring_cycles.amplitude_spectrum IS 'Per-day energy-merged FFT amplitude (Postgres double-precision array), DAY-ALIGNED: element j (0-based) = day period d = j+2; length = floor(range_days/2) − 1 (days 2..N/2). sqrt(Σ amp_k²) over bins k whose period N/k rounds to d. The Fourier REFERENCE for the amp bars of the per-date bar charts — NOT recurrence evidence by itself.';
COMMENT ON COLUMN analysis.recurring_cycles.count_spectrum     IS 'Per-day recurrence COUNT factor, day-aligned like amplitude_spectrum (element j = day j+2). count(d) = recEXT(d) × acfFrac(d): prominence-filtered alternating-extrema evidence (pool hits within ±15% of d over max possible cycles floor((N−d)/d), capped 1) × MA-detrended ACF coherence (fraction of multiples m·d with biased acf ≥ 1.96/√N). Says WHETHER price actually repeated that rise/drop spacing.';
COMMENT ON COLUMN analysis.recurring_cycles.strength_spectrum  IS 'Per-day summarized recurring STRENGTH, day-aligned: strength(d) = (amplitude(d)/σ_band) × count(d), σ_band = sqrt(Σ_{d′≤N/4} amp(d′)² / 2). 0 where not auditable (d > N/3 — under 3 cycles in the window). period_days = argmax + 2.';
COMMENT ON COLUMN analysis.recurring_cycles.significance       IS 'Poisson audit at period_days d*: −log10 of the Bonferroni-adjusted tail p-value P(Poisson(λ̂₀) ≥ hits) — the raw prominence-filtered extrema-pool hit count tested against the chance expectation λ̂₀(d), empirically calibrated per (N, d, pool size) on random-walk + stochastic-vol nulls (analyze/recurring_cycles/poisson_calibration.json). 0 = not significant; ≥ 1.30 ⇔ p < 0.05. Validated FPR ≤ 5% on synthetic nulls at every range_days, power 99%+ on 20d cycles. 0 when period_days = 0.';
COMMENT ON COLUMN analysis.recurring_cycles.evidence_ratio     IS 'hits(d*)/λ̂₀(d*): observed prominence-filtered swing-hit count over the Poisson null expectation at the headline period. 0 when period_days = 0.';
COMMENT ON COLUMN analysis.recurring_cycles.significance_spectrum IS 'Per-day Poisson audit significance (Postgres double-precision array), DAY-ALIGNED like amplitude_spectrum: element j = day period d = j+2; length = floor(range_days/2) − 1. −log10 of the Bonferroni-adjusted tail p-value P(Poisson(λ̂₀(d)) ≥ hits(d)) over the auditable days (d ≤ N/3); 0 where not auditable or not significant; capped at 300. Drives the significance bars of the per-date charts.';
COMMENT ON COLUMN analysis.recurring_cycles.hits_spectrum IS 'Per-day OBSERVED prominence-filtered swing-hit count (Postgres double-precision array, integral values), DAY-ALIGNED: element j = day period d = j+2; length = floor(range_days/2) − 1. The raw recEXT numerator (uncapped) — the observed side of the Poisson audit; evidence = hits/λ̂₀ per day. Drives the hits column of the per-date Poisson audit table.';
COMMENT ON COLUMN analysis.recurring_cycles.lam0_spectrum IS 'Per-day chance expectation λ̂₀(d) of the point-process null (Postgres double-precision array), DAY-ALIGNED: element j = day period d = j+2. The empirically calibrated expected hit rate n_pool × g(pool-bin, d) (random-walk + stochastic-vol nulls, poisson_calibration.json; floored at 1e-3) the observed hits are tested against. Drives the λ̂₀ column of the per-date Poisson audit table.';
COMMENT ON COLUMN analysis.recurring_cycles.last_date          IS 'Last trading date of the sliding window. The window covers the range_days trading days ending on (and including) last_date.';
COMMENT ON COLUMN analysis.recurring_cycles.range_days         IS 'Window size in trading days. Constrained to (20, 60, 255, 500, 750, 1275) — short-term, monthly, yearly, 2-year, 3-year, and 5-year windows.';

-- ----------------------------------------------------------------------------
--  Register in analysis.analysis_identity
-- ----------------------------------------------------------------------------
INSERT INTO analysis.analysis_identity (name, detail_name, summary_name, last_run_datetime, description) VALUES
    ('recurring_cycles', 'recurring_cycles', NULL, NOW(),
     'Per-(sec_type, code, last_date, range_days) recurring rise/drop periodicity of close prices. For each security and trading date, takes the trailing range_days close prices and audits every integer day period d (2..N/2) for RECURRENCE in the time domain: count(d) = prominence-filtered alternating-extrema evidence (swings within ±15% of d) × MA-detrended ACF coherence (multiples m·d with biased acf ≥ 1.96/√N); strength(d) = (amp(d)/σ_band) × count(d) where amp(d) is the energy-merged FFT amplitude (Fourier reference). Headline period_days = argmax of strength (0 = no recurring period — one-off swings/trends gated out by count). Per-day spectra stored day-aligned (element j = day j+2, length floor(range_days/2)−1). FFT used ONLY as the amplitude reference and to compute the ACF (Wiener–Khinchin). POISSON AUDIT (added 2026-09): the raw extrema-pool hit count hits(d) is tested against the chance expectation λ̂₀(d) of a point-process null, empirically calibrated per (N, d, pool size) on random-walk + stochastic-vol nulls (poisson_calibration.json, regenerate via temp_scripts/gen_rc_poisson_calibration.py); significance(d) = −log10 of the Bonferroni p-value P(Poisson(λ̂₀) ≥ hits) over the auditable days (d ≤ N/3) — 0 = not significant, ≥ 1.30 ⇔ p < 0.05 (validated FPR ≤ 5% on nulls, power 99%+ on synthetic 20d cycles); evidence_ratio = hits(d*)/λ̂₀(d*) at the headline period. significance_spectrum, hits_spectrum (observed swing-hit count) and lam0_spectrum (chance expectation λ̂₀(d)) stored day-aligned like the other spectra — the raw observed/expected pair behind the audit. range_days constrained to (20, 60, 255, 500, 750, 1275). Source: index=index_basic_stats.close, etf=COALESCE(etf_adjustment.adj_close, etf_basic_stats.close), stock=stock_basic_stats.close. Built by analyze.recurring_cycles (truncate-then-recompute per sec_type on --force; incremental missing-date upsert otherwise); all INSERTs in Python per project rule. Replaces analysis.fourier_freqs (dropped).')
ON CONFLICT (name) DO UPDATE SET
    detail_name       = EXCLUDED.detail_name,
    summary_name      = EXCLUDED.summary_name,
    last_run_datetime = NOW(),
    description       = EXCLUDED.description;
