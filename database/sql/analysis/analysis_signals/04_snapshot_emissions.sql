-- Snapshot emission attempts (the partial-backfill registry) of
-- analyze.analysis_signals.
--
-- The incremental resolution's PARTIAL BACKFILL leg re-emits the
-- (code, population cohort, snapshot) identity triples that
-- signal_strategies never covered — a snapshot key counts as written
-- the first time ANY code registers (snapshot-granular `have`), so
-- identity rows that join the forecast registry later (a newly listed
-- index; a newly configured derived population like the 2026-09-27
-- ohlcv-boost backfill) never see their frozen snapshot emitted.
--
-- A triple that was ATTEMPTED and legitimately registered nothing
-- (every bucket failed the gate/quality floors — common for the thin
-- ohlcv-selection subset) is indistinguishable from a never-emitted
-- one in the strategies tables alone: without an attempt stamp the
-- resolution would re-scope it as a no-op on EVERY run. One row here
-- per (code, sec_type, signal_type, stat_date, cohort) emission settles
-- the question: the diff runs over identity MINUS covered MINUS
-- attempted. Stamped by the engine after every scope's write (full
-- scopes stamp their whole identity code set; partial scopes their
-- explicit code list); swept with the retired snapshot keys.
--
-- ohlcv_type uses the '' sentinel for the plain parent cohort (the
-- identities registry's NULL) so the PK stays NOT NULL.

CREATE TABLE IF NOT EXISTS analysis_signals.snapshot_emissions (
    code         TEXT      NOT NULL,  -- ticker (same convention as signal_strategies.code)
    sec_type     TEXT      NOT NULL,  -- 'etf' | 'index' | 'stock'
    signal_type  TEXT      NOT NULL,  -- the family's signal_strategies.signal_type
    stat_date    DATE      NOT NULL,  -- the snapshot key emitted (== signal_strategies.end_date)
    ohlcv_type   TEXT      NOT NULL DEFAULT '',  -- the emitted cohort: '' the plain parent (identities' NULL), 'ohlcv_boost' / 'ohlcv_selection' the derived children
    emitted_at   TIMESTAMP NOT NULL DEFAULT NOW(),  -- the emission attempt's wall time (diagnostic only)

    CONSTRAINT pk_snapshot_emissions PRIMARY KEY (code, sec_type, signal_type, stat_date, ohlcv_type),
    CONSTRAINT chk_snapshot_emissions_sec_type CHECK (sec_type IN ('stock', 'etf', 'index'))
);

-- Date-first lookup (the resolution's per-date attempted set).
CREATE INDEX IF NOT EXISTS idx_snapshot_emissions_date
    ON analysis_signals.snapshot_emissions (sec_type, signal_type, stat_date);

COMMENT ON TABLE analysis_signals.snapshot_emissions IS 'Emission ATTEMPTS of analyze.analysis_signals, one row per (code, sec_type, signal_type, snapshot, population cohort) scope the incremental resolution has emitted — full scopes stamp their whole identity code set, partial backfill scopes their explicit code list. Lets the partial-backfill diff (identity MINUS covered MINUS attempted) distinguish a triple that legitimately registered nothing (every bucket failed the gates — routine for the thin ohlcv-selection subset) from a never-emitted one, so neither re-scopes on every run. Maintained by python -m analyze.analysis_signals; retired snapshot keys are swept with the signals rows.';
