
-- ----------------------------------------------------------------------------
-- Table: debt_outright_repo
--   ← PBoC outright-repo tender (bond-buying; longer-tenor liquidity injection)
-- ----------------------------------------------------------------------------
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: The central bank's outright repo purchases — the longer-tenor bond-buying liquidity injections.
-- tags: debt, 债券, central bank, 央行, bond buying, 买断式逆回购, liquidity, 流动性

CREATE TABLE IF NOT EXISTS stats.debt_outright_repo (
    date                      DATE          NOT NULL,
    outright_repo_marker      SMALLINT NOT NULL DEFAULT 0
        CHECK (outright_repo_marker IN (0,1)),
    outright_repo_quantity    NUMERIC(18,4),
    outright_repo_tenor_days  NUMERIC(6,1),
    outright_repo_tenor_label TEXT,
    outright_repo_serial      TEXT,

    CONSTRAINT pk_debt_outright_repo PRIMARY KEY (date),
    CONSTRAINT fk_debt_outright_repo_date FOREIGN KEY (date) REFERENCES stats.debt_identity(date)
);

COMMENT ON TABLE  stats.debt_outright_repo           IS 'PBoC outright-repo tender (bond-buying; longer-tenor liquidity injection).';
COMMENT ON COLUMN stats.debt_outright_repo.outright_repo_marker IS '1 if PBoC announced an outright-repo tender on this date; 0 otherwise.';
