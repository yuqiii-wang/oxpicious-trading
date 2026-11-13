
-- ----------------------------------------------------------------------------
-- Table: debt_shibor
--   ← SHIBOR daily fixings
-- ----------------------------------------------------------------------------
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: The daily SHIBOR interbank offered rates across tenors.
-- tags: debt, 债券, shibor, 拆借利率, interbank, 银行间, rates, 利率

CREATE TABLE IF NOT EXISTS stats.debt_shibor (
    date                      DATE          NOT NULL,
    shibor_o_n                NUMERIC(8,4),
    shibor_1w                 NUMERIC(8,4),
    shibor_2w                 NUMERIC(8,4),
    shibor_1m                 NUMERIC(8,4),
    shibor_3m                 NUMERIC(8,4),
    shibor_6m                 NUMERIC(8,4),
    shibor_9m                 NUMERIC(8,4),
    shibor_1y                 NUMERIC(8,4),

    CONSTRAINT pk_debt_shibor PRIMARY KEY (date),
    CONSTRAINT fk_debt_shibor_date FOREIGN KEY (date) REFERENCES stats.debt_identity(date)
);

COMMENT ON TABLE  stats.debt_shibor                  IS 'SHIBOR daily fixings (all in %).';
