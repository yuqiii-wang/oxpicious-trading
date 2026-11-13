
-- ----------------------------------------------------------------------------
-- Table: debt_repo
--   ← Reverse-repo lifecycle (running cumulative of all outstanding repos)
-- ----------------------------------------------------------------------------
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: The running stock of outstanding central-bank reverse repos — how much short-term liquidity is still deployed.
-- tags: debt, 债券, reverse repo, 逆回购, outstanding balance, 余额

CREATE TABLE IF NOT EXISTS stats.debt_repo (
    date                      DATE          NOT NULL,
    repo_start_quantity       NUMERIC(18,4) NOT NULL DEFAULT 0,
    repo_end_quantity         NUMERIC(18,4) NOT NULL DEFAULT 0,
    repo_net_injection        NUMERIC(18,4) NOT NULL DEFAULT 0,
    repo_cumulative           NUMERIC(18,4) NOT NULL DEFAULT 0,

    CONSTRAINT pk_debt_repo PRIMARY KEY (date),
    CONSTRAINT fk_debt_repo_date FOREIGN KEY (date) REFERENCES stats.debt_identity(date)
);

COMMENT ON TABLE  stats.debt_repo                    IS 'Reverse-repo lifecycle: running cumulative of all outstanding repos.';
COMMENT ON COLUMN stats.debt_repo.repo_cumulative    IS 'Running outstanding reverse-repo balance (亿元) — peak level indicates PBoC net liquidity stance.';
