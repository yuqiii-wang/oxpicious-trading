-- ============================================================================
--  Table: live.live_identity
--    Registry of all live pipelines stored in the `live` schema.
--    Each row corresponds to one or more live result tables.
--
--  Columns:
--    name             — primary pipeline identifier (PK). Matches the suffix
--                       of the main result table when only one exists.
--    detail_name      — optional suffix of the per-tick detail result table
--                       (e.g. 'sec_alloc_live_attribution'). NULL when the
--                       pipeline has no separate detail table.
--    summary_name     — optional suffix of the aggregated summary result table
--                       (e.g. 'sec_alloc_live_prev_ref'). NULL when the
--                       pipeline has no summary table.
--    last_run_datetime — timestamp of the most recent run that recomputed
--                       this pipeline (UTC).
--    description      — free-form description of what the pipeline computes.
--
--  Mirrors analysis.analysis_identity in structure.
-- ============================================================================

CREATE TABLE IF NOT EXISTS live.live_identity (
    name              TEXT         NOT NULL,
    detail_name       TEXT,
    summary_name      TEXT,
    last_run_datetime TIMESTAMP    NOT NULL DEFAULT NOW(),
    description       TEXT,

    CONSTRAINT pk_live_identity PRIMARY KEY (name)
);

-- Add detail_name / summary_name to an existing table (idempotent).
ALTER TABLE live.live_identity
    ADD COLUMN IF NOT EXISTS detail_name TEXT;
ALTER TABLE live.live_identity
    ADD COLUMN IF NOT EXISTS summary_name TEXT;

COMMENT ON TABLE  live.live_identity                     IS 'Registry of live pipelines stored in the live schema. One row per pipeline (may have a detail table, a summary table, or both).';
COMMENT ON COLUMN live.live_identity.name               IS 'Primary pipeline identifier; matches the suffix of the main result table.';
COMMENT ON COLUMN live.live_identity.detail_name        IS 'Optional suffix of the per-tick detail result table (e.g. sec_alloc_live_attribution).';
COMMENT ON COLUMN live.live_identity.summary_name       IS 'Optional suffix of the aggregated summary result table (e.g. sec_alloc_live_prev_ref).';
COMMENT ON COLUMN live.live_identity.last_run_datetime  IS 'Timestamp of the most recent run that recomputed this pipeline (UTC).';
COMMENT ON COLUMN live.live_identity.description        IS 'Free-form description of what the live pipeline computes.';

CREATE INDEX IF NOT EXISTS idx_live_identity_name
    ON live.live_identity (name);
