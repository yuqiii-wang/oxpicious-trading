-- ============================================================================
--  Table: analysis.analysis_identity
--    Registry of all analyses stored in the `analysis` schema.
--    Each row corresponds to one or more analysis result tables.
--
--  Columns:
--    name             — primary analysis identifier (PK). Matches the suffix
--                       of the "main" result table when only one exists.
--    detail_name      — optional suffix of the per-date detail result table
--                       (e.g. 'mov_ave_spreads_detail'). NULL when the analysis
--                       has no detail table.
--    summary_name     — optional suffix of the aggregated summary result table
--                       (e.g. 'mov_ave_spreads_summary'). NULL when the
--                       analysis has no summary table.
--    last_run_datetime — timestamp of the most recent run that recomputed
--                       this analysis (UTC).
--    description      — free-form description of what the analysis computes.
--
--  Example: the mov_ave_spread analysis registers
--    name          = 'mov_ave_spread'
--    detail_name   = 'mov_ave_spreads_detail'   → analysis.mov_ave_spreads_detail
--    summary_name  = 'mov_ave_spreads_summary'  → analysis.mov_ave_spreads_summary
-- ============================================================================

CREATE TABLE IF NOT EXISTS analysis.analysis_identity (
    name              TEXT         NOT NULL,
    detail_name       TEXT,
    summary_name      TEXT,
    last_run_datetime TIMESTAMP    NOT NULL DEFAULT NOW(),
    description       TEXT,

    CONSTRAINT pk_analysis_identity PRIMARY KEY (name)
);

-- Add detail_name / summary_name to an existing table (idempotent — the
-- ADD COLUMN IF NOT EXISTS clause makes this safe to re-run on databases
-- where the table was created by an older version of this script).
ALTER TABLE analysis.analysis_identity
    ADD COLUMN IF NOT EXISTS detail_name TEXT;
ALTER TABLE analysis.analysis_identity
    ADD COLUMN IF NOT EXISTS summary_name TEXT;

COMMENT ON TABLE  analysis.analysis_identity                     IS 'Registry of analyses stored in the analysis schema. One row per analysis (may have a detail table, a summary table, or both).';
COMMENT ON COLUMN analysis.analysis_identity.name               IS 'Primary analysis identifier; matches the suffix of the main result table when only one exists.';
COMMENT ON COLUMN analysis.analysis_identity.detail_name        IS 'Optional suffix of the per-date detail result table (e.g. mov_ave_spreads_detail). NULL when the analysis has no detail table.';
COMMENT ON COLUMN analysis.analysis_identity.summary_name       IS 'Optional suffix of the aggregated summary result table (e.g. mov_ave_spreads_summary). NULL when the analysis has no summary table.';
COMMENT ON COLUMN analysis.analysis_identity.last_run_datetime  IS 'Timestamp of the most recent run that recomputed this analysis (UTC).';
COMMENT ON COLUMN analysis.analysis_identity.description        IS 'Free-form description of what the analysis computes.';

-- Index for lookup by name (PK already covers this, but explicit for clarity)
CREATE INDEX IF NOT EXISTS idx_analysis_identity_name
    ON analysis.analysis_identity (name);
