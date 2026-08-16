-- ============================================================================
--  Fourier Frequency Analysis — per-(sec_type, code, last_date, range_days)
--  dominant cycle detection via real FFT on close prices.
--
--  For each security (index / etf / stock) and each trading date (last_date),
--  takes the trailing `range_days` close prices as a window, detrends
--  (subtracts the mean), applies numpy.rfft, and stores the DOMINANT
--  frequency (the FFT bin with the highest amplitude, excluding the DC
--  component at k=0).
--
--  Table: analysis.fourier_freqs
--    PK: (sec_type, code, last_date, range_days)
--    sec_type ∈ ('index' | 'etf' | 'stock')
--    range_days ∈ (20, 60, 255, 500, 750)
--
--  COLUMNS
--    freq                  — Dominant cycle PERIOD in trading days
--                            (NOT frequency-in-cycles-per-day). Computed as
--                            round(range_days / k*) where k* is the FFT bin
--                            index (1-based, excluding DC) with the highest
--                            amplitude. Minimum = 2 (Nyquist limit — shortest
--                            detectable cycle is 2 days); maximum =
--                            range_days (one full cycle over the window —
--                            longest detectable cycle). Set to range_days
--                            with amplitude 0 when the window is constant
--                            (no periodic signal).
--
--    amplitude_close_price — Amplitude of the dominant frequency component
--                            in ORIGINAL PRICE units (yuan). Computed as
--                            |X[k*]| × 2 / range_days (one-sided amplitude
--                            spectrum). Represents half the peak-to-peak
--                            swing of the dominant sinusoidal component.
--                            0 when the window is constant. (Redundant with
--                            the max of amplitude_spectrum — kept as a
--                            scalar so the lightweight codes/chart endpoints
--                            don't have to scan the array.)
--
--    amplitude_spectrum    — FULL one-sided amplitude spectrum as a Postgres
--                            double-precision array. Element i (0-based) is
--                            |X[i+1]| × 2 / range_days — i.e. the amplitude
--                            of FFT bin k=i+1, EXCLUDING the DC component
--                            (k=0). Array length = floor(range_days / 2).
--                            The dominant component is the max element; its
--                            bin k* = argmax + 1, and the dominant cycle
--                            period = round(range_days / k*). Drives the
--                            per-date full-FFT-spectrum bar charts on the
--                            Fourier Frequencies page (one chart per
--                            range_days window, reactive to a clicked date
--                            on the top index price plot). NULL on legacy
--                            rows written before the column existed; back-
--                            filled by re-running the populator with --force.
--
--    last_date             — Last trading date of the sliding window. The
--                            window covers the `range_days` trading days
--                            ending on (and including) last_date.
--
--    range_days            — Window size in trading days. Constrained to
--                            (20, 60, 255, 500, 750) — short-term intraday
--                            cycles, monthly, yearly, 2-year, and 3-year
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
--    analyze.fourier_freqs (Python module, truncate-then-recompute per
--    sec_type on --force; incremental missing-date upsert otherwise).
--    Per project rule, ALL INSERTs are in Python — no raw INSERT...SELECT
--    SQL in this file. For generic test runs, populate sec_type='index'
--    first; once verified, re-run for etf + stock.
--
--  Register in analysis.analysis_identity (name='fourier_freqs').
-- ============================================================================
CREATE TABLE IF NOT EXISTS analysis.fourier_freqs (
    sec_type        TEXT         NOT NULL,  -- 'index' | 'etf' | 'stock'
    code            TEXT         NOT NULL,

    freq            INTEGER      NOT NULL,  -- dominant cycle PERIOD in DAYS
    amplitude_close_price       NUMERIC(20, 10) NOT NULL,
    -- Full one-sided amplitude spectrum (excludes DC at k=0).
    -- Length = floor(range_days/2); element i = |X[i+1]| × 2 / range_days.
    amplitude_spectrum          DOUBLE PRECISION[],

    last_date       DATE         NOT NULL,
    range_days      INTEGER      NOT NULL,  -- window size in trading days

    CONSTRAINT pk_fourier_freqs PRIMARY KEY (sec_type, code, last_date, range_days),
    CONSTRAINT chk_fourier_freqs_sec_type
        CHECK (sec_type IN ('index', 'etf', 'stock')),
    CONSTRAINT chk_fourier_freqs_range_days
        CHECK (range_days IN (20, 60, 255, 500, 750))
);

-- Indexes for common access patterns:
--   1. Per-security time series (drives per-code cycle charts).
--   2. Per-date snapshot (drives the latest-date cross-sectional view).
--   3. sec_type-scoped scan (test runs populate sec_type='index' first).
CREATE INDEX IF NOT EXISTS idx_fourier_freqs_code_sec_type_last_date
    ON analysis.fourier_freqs (code, sec_type, last_date);
CREATE INDEX IF NOT EXISTS idx_fourier_freqs_last_date
    ON analysis.fourier_freqs (last_date);
CREATE INDEX IF NOT EXISTS idx_fourier_freqs_sec_type_last_date
    ON analysis.fourier_freqs (sec_type, last_date);

-- ----------------------------------------------------------------------------
--  Migration: add amplitude_spectrum to existing installs.
--  Idempotent (IF NOT EXISTS). Fresh installs get the column from CREATE
--  TABLE above; this ALTER covers tables created by an older version of
--  this file. Back-filled by re-running the Python populator with --force.
-- ----------------------------------------------------------------------------
ALTER TABLE analysis.fourier_freqs
    ADD COLUMN IF NOT EXISTS amplitude_spectrum DOUBLE PRECISION[];

COMMENT ON TABLE  analysis.fourier_freqs                       IS 'Per-(sec_type, code, last_date, range_days) dominant Fourier frequency of close prices. For each security and trading date, takes the trailing range_days close prices, detrends (subtracts mean), applies numpy.rfft, and stores the dominant cycle period (freq, in trading days), its amplitude (amplitude_close_price, in yuan), and the FULL one-sided amplitude spectrum (amplitude_spectrum, double-precision array). range_days constrained to (20, 60, 255, 500, 750). Source: index=index_basic_stats.close, etf=COALESCE(etf_adjustment.adj_close, etf_basic_stats.close), stock=stock_basic_stats.close. Built by analyze.fourier_freqs (truncate-then-recompute per sec_type on --force; incremental missing-date upsert otherwise); all INSERTs in Python per project rule.';
COMMENT ON COLUMN analysis.fourier_freqs.sec_type              IS 'Subject security type: index, etf, or stock.';
COMMENT ON COLUMN analysis.fourier_freqs.code                  IS 'Security code (bare index code e.g. 000300; ETF/stock ticker with exchange suffix e.g. 159001.SZ / 600008.SS).';
COMMENT ON COLUMN analysis.fourier_freqs.freq                  IS 'Dominant cycle PERIOD in trading days (NOT cycles-per-day). Computed as round(range_days / k*) where k* is the FFT bin (1-based, excluding DC) with the highest amplitude. Minimum 2 (Nyquist); maximum range_days (one full cycle over the window). Set to range_days with amplitude 0 when the window is constant.';
COMMENT ON COLUMN analysis.fourier_freqs.amplitude_close_price IS 'Amplitude of the dominant frequency component in original price units (yuan). Computed as |X[k*]| × 2 / range_days (one-sided amplitude spectrum). Represents half the peak-to-peak swing of the dominant sinusoidal component. 0 when the window is constant (no periodic signal). Redundant with the max of amplitude_spectrum — kept as a scalar so the lightweight codes/chart endpoints do not have to scan the array.';
COMMENT ON COLUMN analysis.fourier_freqs.amplitude_spectrum    IS 'FULL one-sided amplitude spectrum (Postgres double-precision array). Element i (0-based) = |X[i+1]| × 2 / range_days — the amplitude of FFT bin k=i+1, EXCLUDING the DC component (k=0). Array length = floor(range_days / 2). The dominant component is the max element; its bin k* = argmax + 1 and dominant period = round(range_days / k*). Drives the per-date full-FFT-spectrum bar charts on the Fourier Frequencies page (one chart per range_days window, reactive to a clicked date on the top index price plot). NULL on legacy rows; back-filled by re-running the populator with --force.';
COMMENT ON COLUMN analysis.fourier_freqs.last_date            IS 'Last trading date of the sliding window. The window covers the range_days trading days ending on (and including) last_date.';
COMMENT ON COLUMN analysis.fourier_freqs.range_days            IS 'Window size in trading days. Constrained to (20, 60, 255, 500, 750) — short-term, monthly, yearly, 2-year, and 3-year windows.';

-- ----------------------------------------------------------------------------
--  Register in analysis.analysis_identity
-- ----------------------------------------------------------------------------
INSERT INTO analysis.analysis_identity (name, detail_name, summary_name, last_run_datetime, description) VALUES
    ('fourier_freqs', 'fourier_freqs', NULL, NOW(),
     'Per-(sec_type, code, last_date, range_days) dominant Fourier frequency of close prices. For each security and trading date, takes the trailing range_days close prices, detrends (subtracts mean), applies numpy.rfft, and stores the dominant cycle period (freq, in trading days), its amplitude (amplitude_close_price, in yuan), and the FULL one-sided amplitude spectrum (amplitude_spectrum, double-precision array of length floor(range_days/2), excluding DC). range_days constrained to (20, 60, 255, 500, 750). Source: index=index_basic_stats.close, etf=COALESCE(etf_adjustment.adj_close, etf_basic_stats.close), stock=stock_basic_stats.close. Built by analyze.fourier_freqs (truncate-then-recompute per sec_type on --force; incremental missing-date upsert otherwise); all INSERTs in Python per project rule.')
ON CONFLICT (name) DO UPDATE SET
    detail_name       = EXCLUDED.detail_name,
    summary_name      = EXCLUDED.summary_name,
    last_run_datetime = NOW(),
    description       = EXCLUDED.description;
