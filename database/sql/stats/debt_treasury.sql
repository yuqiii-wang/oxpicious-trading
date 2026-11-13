
-- ----------------------------------------------------------------------------
-- Table: debt_treasury
--   ← China treasury bond yield curve
-- ----------------------------------------------------------------------------
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: China treasury bond yields across maturities per day — the risk-free curve.
-- tags: debt, 债券, treasury, 国债, yield curve, 收益率曲线, rates, 利率

CREATE TABLE IF NOT EXISTS stats.debt_treasury (
    date                      DATE          NOT NULL,
    cb_0d                     NUMERIC(8,4),
    cb_1m                     NUMERIC(8,4),
    cb_2m                     NUMERIC(8,4),
    cb_3m                     NUMERIC(8,4),
    cb_6m                     NUMERIC(8,4),
    cb_9m                     NUMERIC(8,4),
    cb_1y                     NUMERIC(8,4),
    cb_2y                     NUMERIC(8,4),
    cb_3y                     NUMERIC(8,4),
    cb_5y                     NUMERIC(8,4),
    cb_7y                     NUMERIC(8,4),
    cb_10y                    NUMERIC(8,4),
    cb_15y                    NUMERIC(8,4),
    cb_20y                    NUMERIC(8,4),
    cb_30y                    NUMERIC(8,4),
    cb_40y                    NUMERIC(8,4),
    cb_50y                    NUMERIC(8,4),

    CONSTRAINT pk_debt_treasury PRIMARY KEY (date),
    CONSTRAINT fk_debt_treasury_date FOREIGN KEY (date) REFERENCES stats.debt_identity(date)
);

COMMENT ON TABLE  stats.debt_treasury                IS 'China treasury bond yield curve (all in %).';
COMMENT ON COLUMN stats.debt_treasury.cb_30y         IS '30Y CGB yield (%) — proxy for long-duration risk-free rate.';
