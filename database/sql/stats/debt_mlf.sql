
-- ----------------------------------------------------------------------------
-- Table: debt_mlf
--   ← PBoC MLF (Medium-term Lending Facility) tender
-- ----------------------------------------------------------------------------
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: The central bank's medium-term lending facility tenders — the size and rate of each MLF injection.
-- tags: debt, 债券, mlf, 中期借贷便利, central bank, 央行

CREATE TABLE IF NOT EXISTS stats.debt_mlf (
    date                      DATE          NOT NULL,
    mlf_marker                SMALLINT NOT NULL DEFAULT 0
        CHECK (mlf_marker IN (0,1)),
    mlf_quantity              NUMERIC(18,4),
    mlf_tenor_days            NUMERIC(6,1),
    mlf_tenor_label           TEXT,
    mlf_serial                TEXT,

    CONSTRAINT pk_debt_mlf PRIMARY KEY (date),
    CONSTRAINT fk_debt_mlf_date FOREIGN KEY (date) REFERENCES stats.debt_identity(date)
);

COMMENT ON TABLE  stats.debt_mlf                     IS 'PBoC MLF (Medium-term Lending Facility) tender.';
COMMENT ON COLUMN stats.debt_mlf.mlf_marker          IS '1 if PBoC announced an MLF tender on this date; 0 otherwise.';
